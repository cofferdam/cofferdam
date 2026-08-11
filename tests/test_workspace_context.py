"""Working Context: what survives a restart, and what is refreshed rather than stored.

Two properties carry this file.

**Durability.** The store is reopened — a new :class:`WorkspaceStore` over the
same path, which is what a daemon restart actually is — and the objective, the
expected next step and the references are still there. Every persistence test
here goes through a genuine reopen rather than asserting against the object that
did the write, because an in-memory cache would pass the second kind of test and
fail the user.

**Non-durability, which is the harder half.** Task state and the delegated worker
are re-derived on every read. The tests prove it by changing the underlying fact
*behind* the store and re-reading: a task that moves to `completed`, a project
whose delegation changes, a workspace that is renamed out of the config file. If
any of those were cached, the snapshot would keep answering with yesterday's
truth and nothing would say so.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cofferdam.workstation.workspace.errors import (
    ActiveWorkspaceUnset,
    ContextFieldInvalid,
    TaskNotInWorkspace,
    WorkspaceDisabled,
    WorkspaceProjectMissing,
    WorkspaceUnknown,
)
from cofferdam.workstation.workspace.service import (
    TASK_REF_LIVE,
    TASK_REF_MISSING,
    TASK_REF_TERMINAL,
    WorkspaceService,
)
from cofferdam.workstation.workspace.store import (
    MAX_OBJECTIVE_CHARS,
    MAX_OBJECTIVE_HISTORY,
    MAX_REFERENCE_CHARS,
    SCHEMA_VERSION,
    WorkspaceStore,
)


class _Config:
    def __init__(self, home: Path) -> None:
        self.config_dir = home / "config"
        self.state_dir = home / "state"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)


class _Project:
    def __init__(self, project_id, *, enabled=True, adapters=(), delegated=None):
        self.project_id = project_id
        self.display_name = project_id.title()
        self.enabled = enabled
        self.adapters = tuple(adapters)
        self.delegated_adapter = delegated

    def delegation(self):
        if self.delegated_adapter is not None:
            if self.delegated_adapter in self.adapters:
                return self.delegated_adapter, "ok"
            return None, "delegated_adapter_unavailable"
        if not self.adapters:
            return None, "no_adapter"
        if len(self.adapters) == 1:
            return self.adapters[0], "ok"
        return None, "ambiguous_adapter"


class _ProjectRegistry:
    def __init__(self, projects):
        self.projects = tuple(projects)


class _TaskRow:
    def __init__(self, task_id, project_id, state="running", adapter_id="claude-code"):
        self.task_id = task_id
        self.project_id = project_id
        self.state = state
        self.adapter_id = adapter_id
        self.title = "a task"
        self.updated_at = "2026-08-11T10:00:00Z"


class _Tasks:
    """The smallest thing that behaves like Task Core for a reference lookup."""

    def __init__(self):
        self.rows = {}

    def add(self, row):
        self.rows[row.task_id] = row
        return row

    def get_task(self, task_id):
        from cofferdam.workstation.tasks.errors import TaskUnknown

        try:
            return self.rows[task_id]
        except KeyError:
            raise TaskUnknown()


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = _Config(self.home)
        self.tasks = _Tasks()
        self.projects = _ProjectRegistry(
            [_Project("cofferdam", adapters=("claude-code",))]
        )
        self.write_workspaces(
            [{"workspace_id": "cofferdam", "display_name": "Cofferdam", "project_id": "cofferdam"}]
        )
        self.store = WorkspaceStore(self.config)
        self.service = self.make_service(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def make_service(self, store):
        return WorkspaceService(
            self.config, store, projects=lambda: self.projects, tasks=self.tasks
        )

    def write_workspaces(self, entries):
        (self.config.config_dir / "workspaces.json").write_text(
            json.dumps({"workspaces": entries}), encoding="utf-8"
        )

    def reopen(self):
        """What a daemon restart is: a new store and service over the same path."""
        self.store.close()
        self.store = WorkspaceStore(self.config)
        self.service = self.make_service(self.store)
        return self.service


class ActivationTests(_Base):
    def test_nothing_is_active_by_default(self):
        payload = self.service.current()
        self.assertFalse(payload["active"])
        self.assertEqual(payload["problem"], "no_active_workspace")
        self.assertIsNone(payload["workspace"])
        self.assertIsNone(payload["working_context"])

    def test_activate_then_read(self):
        self.service.activate("cofferdam")
        payload = self.service.current()
        self.assertTrue(payload["active"])
        self.assertIsNone(payload["problem"])
        self.assertEqual(payload["workspace"]["workspace_id"], "cofferdam")
        self.assertEqual(payload["workspace"]["project_id"], "cofferdam")

    def test_unknown_workspace_refuses_and_changes_nothing(self):
        with self.assertRaises(WorkspaceUnknown):
            self.service.activate("nope")
        self.assertIsNone(self.store.active_workspace_id())

    def test_disabled_workspace_refuses(self):
        self.write_workspaces(
            [{"workspace_id": "cofferdam", "project_id": "cofferdam", "enabled": False}]
        )
        self.service.reload_workspaces()
        with self.assertRaises(WorkspaceDisabled):
            self.service.activate("cofferdam")

    def test_activation_refuses_when_the_project_is_missing(self):
        """Refused now rather than at the next task.

        Activating a workspace whose project cannot be used would make "what are
        we working on" name something nothing can run in.
        """
        self.projects = _ProjectRegistry([])
        with self.assertRaises(WorkspaceProjectMissing):
            self.service.activate("cofferdam")
        self.assertIsNone(self.store.active_workspace_id())

    def test_activation_refuses_when_the_project_is_disabled(self):
        self.projects = _ProjectRegistry([_Project("cofferdam", enabled=False)])
        with self.assertRaises(WorkspaceProjectMissing):
            self.service.activate("cofferdam")

    def test_deactivate_keeps_context(self):
        self.service.activate("cofferdam")
        self.service.set_objective("Implement M2J PR1.")
        self.service.deactivate()
        self.assertFalse(self.service.current()["active"])
        self.service.activate("cofferdam")
        self.assertEqual(
            self.service.current()["working_context"]["objective"], "Implement M2J PR1."
        )

    def test_switching_workspaces_does_not_leak_context(self):
        """The reason context is keyed by workspace rather than stored once."""
        self.write_workspaces(
            [
                {"workspace_id": "cofferdam", "project_id": "cofferdam"},
                {"workspace_id": "research", "project_id": "cofferdam"},
            ]
        )
        self.service.reload_workspaces()
        self.service.activate("cofferdam")
        self.service.set_objective("Implement M2J PR1.")
        self.service.update_context({"expected_next_step": "Write the tests."})

        self.service.activate("research")
        context = self.service.current()["working_context"]
        self.assertIsNone(context["objective"])
        self.assertIsNone(context["expected_next_step"])

        self.service.set_objective("Compare browser actuators.")
        self.service.activate("cofferdam")
        context = self.service.current()["working_context"]
        self.assertEqual(context["objective"], "Implement M2J PR1.")
        self.assertEqual(context["expected_next_step"], "Write the tests.")


class ObjectiveTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.service.activate("cofferdam")

    def test_set_and_read(self):
        self.service.set_objective("Implement M2J workspace foundation.")
        context = self.service.current()["working_context"]
        self.assertEqual(context["objective"], "Implement M2J workspace foundation.")
        self.assertIsNotNone(context["objective_set_at"])
        self.assertEqual(context["objective_source"], "user")

    def test_replacing_an_objective_records_the_previous_one(self):
        self.service.set_objective("First goal.")
        self.service.set_objective("Second goal.")
        history = self.service.objective_history()["history"]
        self.assertEqual([entry["objective"] for entry in history], ["First goal."])
        self.assertEqual(
            self.service.current()["working_context"]["objective"], "Second goal."
        )

    def test_setting_the_same_objective_twice_adds_no_history(self):
        self.service.set_objective("Same goal.")
        self.service.set_objective("Same goal.")
        self.assertEqual(self.service.objective_history()["history"], [])

    def test_clearing_an_objective_keeps_it_in_history(self):
        self.service.set_objective("A goal.")
        self.service.set_objective(None)
        self.assertIsNone(self.service.current()["working_context"]["objective"])
        self.assertEqual(
            [e["objective"] for e in self.service.objective_history()["history"]], ["A goal."]
        )

    def test_history_is_bounded_and_pruned_in_place(self):
        for index in range(MAX_OBJECTIVE_HISTORY + 15):
            self.service.set_objective("goal %d" % index)
        rows = self.store.objective_history("cofferdam", limit=MAX_OBJECTIVE_HISTORY)
        self.assertLessEqual(len(rows), MAX_OBJECTIVE_HISTORY)
        # The newest survive, not the oldest.
        self.assertIn(str(MAX_OBJECTIVE_HISTORY + 13), rows[0].objective)

    def test_an_over_long_objective_is_refused_not_truncated(self):
        """Authored text is refused; storing half of it would show a sentence
        the person did not write."""
        with self.assertRaises(ContextFieldInvalid):
            self.service.set_objective("x" * (MAX_OBJECTIVE_CHARS + 1))
        self.assertIsNone(self.service.current()["working_context"]["objective"])

    def test_whitespace_only_clears(self):
        self.service.set_objective("A goal.")
        self.service.set_objective("   ")
        self.assertIsNone(self.service.current()["working_context"]["objective"])

    def test_control_characters_are_stripped(self):
        self.service.set_objective("clean\x00\x07 text")
        self.assertEqual(
            self.service.current()["working_context"]["objective"], "clean text"
        )

    def test_turkish_text_survives_intact(self):
        self.service.set_objective("İkinci seçeneği seç ve doğrula.")
        self.assertEqual(
            self.service.current()["working_context"]["objective"],
            "İkinci seçeneği seç ve doğrula.",
        )

    def test_a_non_string_objective_is_refused(self):
        for bad in (7, [], {}, True):
            with self.subTest(value=bad):
                with self.assertRaises(ContextFieldInvalid):
                    self.service.set_objective(bad)

    def test_objective_requires_an_active_workspace(self):
        self.service.deactivate()
        with self.assertRaises(ActiveWorkspaceUnset):
            self.service.set_objective("nowhere")


class ContextFieldTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.service.activate("cofferdam")

    def test_set_and_clear_each_reference(self):
        for field in (
            "plan_checkpoint",
            "pending_decision_ref",
            "latest_evidence_ref",
            "expected_next_step",
        ):
            with self.subTest(field=field):
                self.service.update_context({field: "value-" + field})
                self.assertEqual(
                    self.service.current()["working_context"][field], "value-" + field
                )
                self.service.update_context({field: None})
                self.assertIsNone(self.service.current()["working_context"][field])

    def test_update_is_partial(self):
        """Absence leaves alone; null clears. The distinction is the contract."""
        self.service.set_objective("A goal.")
        self.service.update_context({"expected_next_step": "Do the thing."})
        self.service.update_context({"plan_checkpoint": "ROADMAP#m2j"})
        context = self.service.current()["working_context"]
        self.assertEqual(context["objective"], "A goal.")
        self.assertEqual(context["expected_next_step"], "Do the thing.")
        self.assertEqual(context["plan_checkpoint"], "ROADMAP#m2j")

    def test_unknown_field_is_refused(self):
        with self.assertRaises(ContextFieldInvalid):
            self.store.update_context("cofferdam", fields={"objective": "via the wrong door"})

    def test_references_are_bounded(self):
        with self.assertRaises(ContextFieldInvalid):
            self.service.update_context({"plan_checkpoint": "x" * (MAX_REFERENCE_CHARS + 1)})

    def test_revision_advances_on_write(self):
        before = self.service.current()["working_context"]["revision"]
        self.service.update_context({"expected_next_step": "Something."})
        after = self.service.current()["working_context"]["revision"]
        self.assertGreater(after, before)

    def test_expected_next_step_executes_nothing(self):
        """It is a note for a person. Recorded as a test because the name of the
        field is the kind that invites somebody to wire it up later."""
        self.service.update_context({"expected_next_step": "Run the validation pass."})
        payload = self.service.current()
        self.assertEqual(
            payload["working_context"]["expected_next_step"], "Run the validation pass."
        )
        # Nothing was created, started or scheduled by storing a sentence.
        self.assertEqual(self.tasks.rows, {})


class ActiveTaskAuthorityTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.service.activate("cofferdam")

    def test_pointing_at_a_real_task(self):
        self.tasks.add(_TaskRow("t-1", "cofferdam", state="running"))
        self.service.update_context({"active_task_id": "t-1"})
        task = self.service.current()["working_context"]["active_task"]
        self.assertEqual(task["task_id"], "t-1")
        self.assertEqual(task["status"], TASK_REF_LIVE)
        self.assertEqual(task["state"], "running")
        self.assertFalse(task["terminal"])

    def test_task_state_is_derived_not_stored(self):
        """The central authority proof: change the task behind the store and the
        snapshot changes with it, with no workspace write in between."""
        row = self.tasks.add(_TaskRow("t-1", "cofferdam", state="running"))
        self.service.update_context({"active_task_id": "t-1"})
        self.assertEqual(
            self.service.current()["working_context"]["active_task"]["state"], "running"
        )
        row.state = "completed"
        task = self.service.current()["working_context"]["active_task"]
        self.assertEqual(task["state"], "completed")
        self.assertEqual(task["status"], TASK_REF_TERMINAL)
        self.assertTrue(task["terminal"])

    def test_a_completed_task_is_not_erased(self):
        """It finished, which is the fact somebody came back to read."""
        row = self.tasks.add(_TaskRow("t-1", "cofferdam", state="running"))
        self.service.update_context({"active_task_id": "t-1"})
        row.state = "completed"
        self.reopen()
        task = self.service.current()["working_context"]["active_task"]
        self.assertEqual(task["task_id"], "t-1")
        self.assertEqual(task["status"], TASK_REF_TERMINAL)

    def test_cancelled_and_failed_render_truthfully(self):
        for state in ("cancelled", "failed", "interrupted"):
            with self.subTest(state=state):
                self.tasks.rows.clear()
                self.tasks.add(_TaskRow("t-" + state, "cofferdam", state=state))
                self.service.update_context({"active_task_id": "t-" + state})
                task = self.service.current()["working_context"]["active_task"]
                self.assertEqual(task["state"], state)
                self.assertEqual(task["status"], TASK_REF_TERMINAL)

    def test_a_vanished_task_is_reported_not_invented(self):
        self.tasks.add(_TaskRow("t-1", "cofferdam"))
        self.service.update_context({"active_task_id": "t-1"})
        del self.tasks.rows["t-1"]
        task = self.service.current()["working_context"]["active_task"]
        self.assertEqual(task["task_id"], "t-1")
        self.assertEqual(task["status"], TASK_REF_MISSING)
        self.assertIsNone(task["state"])

    def test_pointing_at_an_unknown_task_is_refused(self):
        with self.assertRaises(ContextFieldInvalid):
            self.service.update_context({"active_task_id": "no-such-task"})
        self.assertIsNone(self.service.current()["working_context"]["active_task"])

    def test_pointing_at_another_projects_task_is_refused(self):
        """Cross-workspace confusion, refused where it is still visible."""
        self.tasks.add(_TaskRow("t-other", "some-other-project"))
        with self.assertRaises(TaskNotInWorkspace):
            self.service.update_context({"active_task_id": "t-other"})

    def test_clearing_the_task_reference(self):
        self.tasks.add(_TaskRow("t-1", "cofferdam"))
        self.service.update_context({"active_task_id": "t-1"})
        self.service.update_context({"active_task_id": None})
        self.assertIsNone(self.service.current()["working_context"]["active_task"])

    def test_no_provider_session_id_anywhere_in_the_payload(self):
        self.tasks.add(_TaskRow("t-1", "cofferdam"))
        self.service.update_context({"active_task_id": "t-1"})
        serialized = json.dumps(self.service.current())
        for forbidden in ("provider_session", "session_id", "prompt"):
            self.assertNotIn(forbidden, serialized)


class DelegatedWorkerAuthorityTests(_Base):
    def test_worker_is_derived_from_the_project(self):
        self.service.activate("cofferdam")
        workspace = self.service.current()["workspace"]
        self.assertEqual(workspace["delegated_worker"], "claude-code")
        self.assertEqual(workspace["delegation"], "ok")
        self.assertTrue(workspace["worker_available"])

    def test_changing_the_project_changes_the_worker_with_no_workspace_write(self):
        self.service.activate("cofferdam")
        self.projects = _ProjectRegistry(
            [_Project("cofferdam", adapters=("claude-code", "claude-agent-sdk"),
                      delegated="claude-agent-sdk")]
        )
        workspace = self.service.current()["workspace"]
        self.assertEqual(workspace["delegated_worker"], "claude-agent-sdk")

    def test_ambiguous_delegation_is_reported_not_guessed(self):
        self.service.activate("cofferdam")
        self.projects = _ProjectRegistry(
            [_Project("cofferdam", adapters=("claude-agent-sdk", "claude-code"))]
        )
        workspace = self.service.current()["workspace"]
        self.assertIsNone(workspace["delegated_worker"])
        self.assertEqual(workspace["delegation"], "ambiguous_adapter")
        self.assertFalse(workspace["worker_available"])

    def test_the_worker_is_never_persisted(self):
        """Proved against the database rather than the API: no column holds it."""
        self.service.activate("cofferdam")
        row = self.store.context("cofferdam")
        self.assertFalse(
            any("adapter" in field or "worker" in field for field in vars(row))
        )


class DurabilityTests(_Base):
    def test_everything_persisted_survives_a_reopen(self):
        self.tasks.add(_TaskRow("t-1", "cofferdam", state="running"))
        self.service.activate("cofferdam")
        self.service.set_objective("Implement M2J workspace foundation.")
        self.service.update_context(
            {
                "active_task_id": "t-1",
                "expected_next_step": "Open the PR.",
                "plan_checkpoint": "ROADMAP#m2j-pr1",
                "pending_decision_ref": "decision-7",
                "latest_evidence_ref": "evidence-3",
            }
        )

        self.reopen()

        payload = self.service.current()
        self.assertTrue(payload["active"])
        self.assertEqual(payload["workspace"]["workspace_id"], "cofferdam")
        context = payload["working_context"]
        self.assertEqual(context["objective"], "Implement M2J workspace foundation.")
        self.assertEqual(context["expected_next_step"], "Open the PR.")
        self.assertEqual(context["plan_checkpoint"], "ROADMAP#m2j-pr1")
        self.assertEqual(context["pending_decision_ref"], "decision-7")
        self.assertEqual(context["latest_evidence_ref"], "evidence-3")
        self.assertEqual(context["active_task"]["task_id"], "t-1")

    def test_objective_history_survives_a_reopen(self):
        self.service.activate("cofferdam")
        self.service.set_objective("First.")
        self.service.set_objective("Second.")
        self.reopen()
        history = self.service.objective_history()["history"]
        self.assertEqual([entry["objective"] for entry in history], ["First."])

    def test_a_workspace_removed_from_config_is_reported_not_replaced(self):
        self.service.activate("cofferdam")
        self.service.set_objective("A goal.")
        self.write_workspaces(
            [{"workspace_id": "other", "project_id": "cofferdam"}]
        )
        self.reopen()
        payload = self.service.current()
        self.assertFalse(payload["active"])
        self.assertEqual(payload["problem"], "active_workspace_unconfigured")
        self.assertEqual(payload["workspace"]["workspace_id"], "cofferdam")

    def test_a_workspace_disabled_after_activation_is_reported(self):
        self.service.activate("cofferdam")
        self.write_workspaces(
            [{"workspace_id": "cofferdam", "project_id": "cofferdam", "enabled": False}]
        )
        self.reopen()
        payload = self.service.current()
        self.assertFalse(payload["active"])
        self.assertEqual(payload["problem"], "active_workspace_disabled")

    def test_a_project_removed_after_activation_is_reported(self):
        self.service.activate("cofferdam")
        self.projects = _ProjectRegistry([])
        payload = self.service.current()
        self.assertEqual(payload["problem"], "active_workspace_project_missing")
        self.assertFalse(payload["workspace"]["project_available"])
        self.assertIsNone(payload["workspace"]["delegated_worker"])

    def test_database_permissions_are_owner_only(self):
        self.service.activate("cofferdam")
        self.service.set_objective("A goal.")
        mode = self.store.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)

    def test_no_database_is_created_until_something_is_written(self):
        """A host that never activates a workspace never gains a file."""
        fresh = WorkspaceStore(self.config, path=self.home / "state" / "unused" / "w.sqlite3")
        try:
            self.assertFalse(fresh.path.exists())
        finally:
            fresh.close()

    def test_schema_version_is_recorded(self):
        self.service.activate("cofferdam")
        self.assertEqual(self.store.health()["schema_version"], SCHEMA_VERSION)
        self.assertTrue(self.store.health()["available"])

    def test_a_newer_database_is_refused_rather_than_downgraded(self):
        from cofferdam.workstation.tasks.errors import StoreUnavailable

        self.service.activate("cofferdam")
        with self.store._read() as connection:  # noqa: SLF001 - asserting durable behaviour
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION + 1),),
            )
        self.store.close()
        reopened = WorkspaceStore(self.config)
        try:
            with self.assertRaises(StoreUnavailable):
                reopened.active_workspace_id()
        finally:
            reopened.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
