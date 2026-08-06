"""Task Core: identity, projects, lifecycle, persistence, restart, adapters.

The properties under test are the ones a task system gets wrong quietly:

* an id that carries something it should not, or that changes across a restart;
* a state that moved somewhere the graph forbids, or a snapshot that disagrees
  with the history it is supposed to summarise;
* a task reported as running by a process that is not running it;
* content — a prompt, a follow-up, a result — reaching a log or an audit record.

Everything here runs against the real store, the real graph and the shipped
validation adapter. See ``tests/_task_doubles.py`` for what is faked and why.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import unittest
from pathlib import Path

from cofferdam.workstation.tasks import errors as task_errors
from cofferdam.workstation.tasks.adapters import build_registry
from cofferdam.workstation.tasks.adapters.validation import (
    SCENARIOS,
    ValidationTaskAdapter,
    scenario_for,
)
from cofferdam.workstation.tasks.identity import (
    TASK_ID_PREFIX,
    new_correlation_id,
    new_task_id,
    valid_correlation_id,
    valid_task_id,
)
from cofferdam.workstation.tasks.lifecycle import (
    ALLOWED_TRANSITIONS,
    IllegalTransition,
    can_transition,
    check_transition,
)
from cofferdam.workstation.tasks.models import (
    CORE_OWNED_EVENT_TYPES,
    MAX_EVENT_PAGE,
    MAX_PROMPT_CHARS,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
    STATES,
    TERMINAL_STATES,
    valid_user_text,
)
from cofferdam.workstation.tasks.projects import (
    FORBIDDEN_PROJECT_FIELDS,
    load_projects,
    verify_root,
)
from cofferdam.workstation.tasks.store import TaskStore, database_permissions

from ._task_doubles import (
    PROJECT_ID,
    TURKISH_PROMPT,
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    ScriptedAdapter,
    TaskTestCase,
    python_code_only,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# -- 1-3: identity -----------------------------------------------------------


class TaskIdentity(unittest.TestCase):
    def test_ids_are_unique(self):
        """1. Ten thousand ids, no collisions."""
        ids = {new_task_id() for _ in range(10000)}
        self.assertEqual(len(ids), 10000)

    def test_ids_are_not_derived_from_prompt_text(self):
        """1. The same prompt twice must not produce the same id.

        Asserted as a *property of the function's inputs* as well: it takes no
        prompt, so there is no argument a prompt could arrive in. That is the
        stronger half — a check could be deleted; a missing parameter could not.
        """
        import inspect

        self.assertEqual(set(inspect.signature(new_task_id).parameters), {"now_ms"})
        first, second = new_task_id(), new_task_id()
        self.assertNotEqual(first, second)

    def test_ids_from_the_same_millisecond_still_differ(self):
        """Sortability must not have been bought with randomness."""
        ids = {new_task_id(now_ms=1_700_000_000_000) for _ in range(2000)}
        self.assertEqual(len(ids), 2000)

    def test_ids_sort_by_creation_time(self):
        earlier = new_task_id(now_ms=1_700_000_000_000)
        later = new_task_id(now_ms=1_700_000_001_000)
        self.assertLess(earlier, later)

    def test_ids_are_opaque_and_shaped(self):
        task_id = new_task_id()
        self.assertTrue(task_id.startswith(TASK_ID_PREFIX))
        self.assertTrue(valid_task_id(task_id))
        # Safe as a database key and in a URL path without escaping.
        self.assertRegex(task_id, r"^task_[0-9a-hjkmnp-tv-z]{26}$")

    def test_malformed_ids_are_refused(self):
        for hostile in (
            "",
            None,
            123,
            "task_",
            "task_" + "z" * 25,
            "task_" + "z" * 27,
            "../../etc/passwd",
            "task_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "task_iiiiiiiiiiiiiiiiiiiiiiiiii",  # 'i' is not in the alphabet
        ):
            self.assertFalse(valid_task_id(hostile), repr(hostile))

    def test_correlation_ids_are_bounded_and_valid(self):
        """3."""
        for _ in range(100):
            value = new_correlation_id()
            self.assertTrue(valid_correlation_id(value))
            self.assertLessEqual(len(value), 40)
        self.assertFalse(valid_correlation_id("tcor-nothex!!!!!"))


class TaskIdentityAcrossRestart(TaskTestCase):
    def test_task_ids_survive_a_restart(self):
        """2. The id in the database is the id after the daemon comes back."""
        row = self.create()
        task_id = row.task_id
        self.restart()
        self.assertEqual(self.store.get(task_id).task_id, task_id)
        self.assertEqual(self.store.get(task_id).correlation_id, row.correlation_id)


# -- 4-9: authority ----------------------------------------------------------


class RequestAuthority(TaskTestCase):
    def test_the_client_cannot_choose_an_origin(self):
        """4. Origin is a parameter of the service, never a request field."""
        import inspect

        signature = inspect.signature(self.service.create_task)
        self.assertIn("origin", signature.parameters)
        # And it is keyword-only with a server default, so a body cannot reach it.
        self.assertEqual(
            signature.parameters["origin"].kind, inspect.Parameter.KEYWORD_ONLY
        )
        self.assertEqual(self.create().origin, "pwa")

    def test_there_is_no_field_for_a_return_url_or_a_working_directory(self):
        """5, 6, 7. Absent rather than validated — asserted on the signature."""
        import inspect

        allowed = set(inspect.signature(self.service.create_task).parameters)
        for forbidden in (
            "return_url",
            "callback_url",
            "webhook_url",
            "working_directory",
            "cwd",
            "path",
            "root",
            "executable",
            "command",
            "argv",
            "env",
            "environment",
            "shell",
            "pid",
            "unit",
        ):
            self.assertNotIn(forbidden, allowed, forbidden + " is accepted")

    def test_unknown_project_is_refused(self):
        """8."""
        with self.assertRaises(task_errors.ProjectUnknown):
            self.create(project_id="not-a-project")

    def test_disabled_project_is_refused(self):
        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "root": str(self.project_root),
                    "adapters": ["validation"],
                    "enabled": False,
                }
            ]
        )
        self.service.reload_projects()
        with self.assertRaises(task_errors.ProjectDisabled):
            self.create()

    def test_unknown_adapter_is_refused(self):
        """10."""
        with self.assertRaises(task_errors.AdapterUnknown):
            self.create(adapter_id="claude-code")

    def test_an_adapter_the_project_does_not_list_is_refused(self):
        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "root": str(self.project_root),
                    "adapters": [],
                }
            ]
        )
        self.service.reload_projects()
        with self.assertRaises(task_errors.AdapterNotPermitted):
            self.create()

    def test_the_adapter_receives_the_server_resolved_root(self):
        """The adapter's view of where it runs is Cofferdam's, not the phone's."""
        scripted = self.install_adapter(ScriptedAdapter())
        self.create(adapter_id="scripted")
        self.assertEqual(scripted.contexts[0].project_root, self.project_root)


class ProjectRoots(TaskTestCase):
    def test_a_symlinked_root_is_refused(self):
        """9. The check that stops a project pointing into somebody else's home."""
        real = self.home / "elsewhere"
        real.mkdir()
        link = self.home / "linked"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(task_errors.ProjectRootInvalid):
            verify_root(link)

    def test_a_root_reached_through_a_symlinked_parent_is_refused(self):
        real = self.home / "outside"
        real.mkdir()
        (real / "inner").mkdir()
        link = self.home / "bridge"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(task_errors.ProjectRootInvalid):
            verify_root(link / "inner")

    def test_a_missing_root_is_refused(self):
        with self.assertRaises(task_errors.ProjectRootInvalid):
            verify_root(self.home / "never-existed")

    def test_a_file_is_not_a_root(self):
        target = self.home / "a-file"
        target.write_text("x", encoding="utf-8")
        with self.assertRaises(task_errors.ProjectRootInvalid):
            verify_root(target)

    def test_a_root_deleted_after_load_is_refused_at_creation(self):
        """Verified at use, not only at load: directories move."""
        import shutil

        shutil.rmtree(self.project_root)
        with self.assertRaises(task_errors.ProjectRootInvalid):
            self.create()

    def test_relative_and_expanding_roots_are_rejected_at_load(self):
        for hostile in ("relative/path", "~/cofferdam", "$HOME/x", "/a/../b", ""):
            self.write_projects(
                [{"project_id": "p", "root": hostile, "adapters": ["validation"]}]
            )
            registry = load_projects(self.config, ("validation",))
            self.assertEqual(registry.projects, (), hostile)
            self.assertTrue(registry.problems, hostile)

    def test_a_project_entry_cannot_carry_a_command(self):
        """Configuration says *where*, never *what to run*."""
        for field in sorted(FORBIDDEN_PROJECT_FIELDS)[:6]:
            self.write_projects(
                [
                    {
                        "project_id": "p",
                        "root": str(self.project_root),
                        "adapters": ["validation"],
                        field: "anything",
                    }
                ]
            )
            registry = load_projects(self.config, ("validation",))
            self.assertEqual(registry.projects, (), field)

    def test_the_published_project_never_carries_a_path(self):
        registry = load_projects(self.config, ("validation",))
        blob = json.dumps(registry.to_dict())
        self.assertNotIn(str(self.project_root), blob)
        self.assertNotIn("root", json.dumps(registry.projects[0].to_dict()))

    def test_a_missing_project_file_is_not_an_error(self):
        (self.config.config_dir / "task-projects.json").unlink()
        registry = load_projects(self.config, ("validation",))
        self.assertEqual(registry.projects, ())
        self.assertFalse(registry.source_present)

    def test_one_broken_project_does_not_take_the_others_down(self):
        self.write_projects(
            [
                {"project_id": "broken", "root": "relative", "adapters": []},
                {
                    "project_id": PROJECT_ID,
                    "root": str(self.project_root),
                    "adapters": ["validation"],
                },
            ]
        )
        registry = load_projects(self.config, ("validation",))
        self.assertEqual([p.project_id for p in registry.projects], [PROJECT_ID])
        self.assertEqual(len(registry.problems), 1)


# -- 11-13: the validation adapter's boundary --------------------------------


class ValidationAdapterBoundary(unittest.TestCase):
    def test_it_is_absent_by_default(self):
        """11. Not registered, so there is nothing for a request to reach."""
        self.assertEqual(build_registry().ids(), ())
        self.assertIsNone(build_registry().find("validation"))

    def test_it_requires_explicit_server_side_enablement(self):
        """12."""
        self.assertEqual(
            build_registry(enable_validation_adapter=True).ids(), ("validation",)
        )

    def test_the_default_config_does_not_enable_it(self):
        import tempfile

        from cofferdam.workstation.config import load_config

        with tempfile.TemporaryDirectory() as home:
            self.assertFalse(load_config(Path(home)).enable_validation_task_adapter)

    def test_it_cannot_run_a_shell_a_process_or_the_network(self):
        """13. Structural: the module imports nothing that could.

        Scanned as code with comments and docstrings stripped, so the paragraph
        explaining that it does not use subprocess is not itself a match.
        """
        source = python_code_only(
            (
                REPO_ROOT
                / "cofferdam"
                / "workstation"
                / "tasks"
                / "adapters"
                / "validation.py"
            ).read_text("utf-8")
        )
        for forbidden in (
            "subprocess",
            "os.system",
            "popen",
            "socket",
            "urllib",
            "requests",
            "httpx",
            "shutil",
            "eval(",
            "exec(",
            "__import__",
            "open(",
            "write_text",
            "write_bytes",
            "mkdir",
        ):
            self.assertNotIn(forbidden, source, forbidden + " is reachable")

    def test_it_is_never_called_an_agent_or_a_model(self):
        """A test adapter that read as a real one would be the worst outcome."""
        adapter = ValidationTaskAdapter()
        described = json.dumps(adapter.describe()).lower()
        for forbidden in ("claude", "gpt", "openai", "anthropic", "cursor"):
            self.assertNotIn(forbidden, described)
        self.assertIn("validation", adapter.display_name.lower())
        self.assertTrue(adapter.describe()["validation_only"])

    def test_scenarios_are_code_owned(self):
        """53. A client picks from five words; it cannot supply behaviour."""
        self.assertEqual(
            set(SCENARIOS), {"complete", "wait", "fail", "cancel", "interrupt"}
        )
        for hostile in (
            "scenario: rm -rf /",
            "scenario: {\"delay\": 900}",
            "scenario: ../../etc",
            "scenario:",
            "",
            "anything at all",
        ):
            self.assertIn(scenario_for(hostile), SCENARIOS)
            self.assertEqual(scenario_for(hostile), "complete")

    def test_it_declares_no_restart_recovery(self):
        """It cannot reattach, and says so — which is what makes interruption honest."""
        self.assertFalse(ValidationTaskAdapter().capabilities().recover_after_restart)


# -- 14-19: the state machine and its storage --------------------------------


class StateMachine(unittest.TestCase):
    def test_legal_transitions_succeed(self):
        """14."""
        for start, target in (
            ("created", "queued"),
            ("queued", "starting"),
            ("starting", "running"),
            ("running", "waiting_for_user"),
            ("waiting_for_user", "running"),
            ("running", "completed"),
            ("running", "failed"),
            ("running", "cancelling"),
            ("cancelling", "cancelled"),
        ):
            check_transition(start, target)

    def test_illegal_transitions_are_rejected(self):
        """15, 16. Including every way back out of a terminal state."""
        for start, target in (
            ("completed", "running"),
            ("cancelled", "completed"),
            ("failed", "running"),
            ("created", "completed"),
            ("waiting_for_user", "completed"),
            ("interrupted", "running"),
            ("completed", "completed"),
            ("failed", "failed"),
        ):
            with self.assertRaises(IllegalTransition, msg=start + "->" + target):
                check_transition(start, target)

    def test_every_terminal_state_is_a_dead_end(self):
        for state in TERMINAL_STATES:
            self.assertEqual(ALLOWED_TRANSITIONS[state], frozenset(), state)

    def test_every_state_appears_in_the_graph(self):
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(STATES))

    def test_every_non_terminal_state_can_be_interrupted(self):
        """A restart can happen at any moment, so the graph must express it."""
        for state in STATES:
            if state in TERMINAL_STATES:
                continue
            self.assertTrue(
                can_transition(state, STATE_INTERRUPTED)
                or can_transition(state, "recovery_required"),
                state,
            )

    def test_an_unknown_state_is_refused(self):
        for hostile in ("", "COMPLETED", "running ", "deleted", None, 7):
            with self.assertRaises(IllegalTransition):
                check_transition("running", hostile)


class TransactionalStorage(TaskTestCase):
    def test_state_and_event_land_together(self):
        """17. A refused transition writes neither."""
        row = self.create()
        before_state = row.state
        before_events = len(self.store.events(row.task_id, limit=200))
        with self.assertRaises(IllegalTransition):
            self.store.transition(
                row.task_id,
                STATE_RUNNING,
                event_type="task_started",
                actor="system",
                source="cofferdam",
            )
        after = self.store.get(row.task_id)
        self.assertEqual(after.state, before_state)
        self.assertEqual(len(self.store.events(row.task_id, limit=200)), before_events)

    def test_every_state_change_has_an_event(self):
        """17. The snapshot and the history cannot disagree."""
        row = self.create()
        events = self.store.events(row.task_id, limit=200)
        with_state = [event for event in events if event.state]
        # The final event's state is the task's state, and every revision is
        # represented — no state moved without leaving a record.
        self.assertEqual(with_state[-1].state, row.state)
        revisions = sorted({event.lifecycle_revision for event in events})
        self.assertEqual(revisions, list(range(0, row.lifecycle_revision + 1)))

    def test_event_sequence_is_monotonic(self):
        """18."""
        row = self.create()
        sequences = [event.sequence for event in self.store.events(row.task_id, limit=200)]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))

    def test_concurrent_appends_do_not_collide(self):
        """18. Two writers, one sequence space."""
        row = self.create(prompt="scenario: cancel")
        errors = []

        def append(index):
            try:
                for _ in range(20):
                    self.store.append_event(
                        row.task_id, "progress", actor="adapter", source="adapter",
                        text="tick " + str(index),
                    )
            except Exception as exc:  # pragma: no cover - the failure being tested
                errors.append(exc)

        threads = [threading.Thread(target=append, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [])
        sequences = [e.sequence for e in self.store.events(row.task_id, limit=MAX_EVENT_PAGE)]
        self.assertEqual(len(sequences), len(set(sequences)))
        self.assertEqual(sequences, sorted(sequences))

    def test_event_pagination_is_bounded(self):
        """19. There is no "all"."""
        row = self.create(prompt="scenario: cancel")
        for index in range(MAX_EVENT_PAGE + 50):
            self.store.append_event(
                row.task_id, "progress", actor="adapter", source="adapter",
                text="tick " + str(index),
            )
        self.assertEqual(len(self.store.events(row.task_id, limit=10**6)), MAX_EVENT_PAGE)
        self.assertEqual(len(self.store.events(row.task_id, limit=-5)), 1)

    def test_pagination_after_a_cursor_returns_only_newer_events(self):
        row = self.create()
        everything = self.store.events(row.task_id, limit=200)
        tail = self.store.events(row.task_id, after=everything[2].sequence, limit=200)
        self.assertTrue(all(event.sequence > everything[2].sequence for event in tail))
        self.assertEqual(len(tail), len(everything) - 3)

    def test_the_database_is_not_world_readable(self):
        self.create()
        mode = database_permissions(self.store.path)
        self.assertIsNotNone(mode)
        self.assertEqual(mode & (stat.S_IRWXG | stat.S_IRWXO), 0)

    def test_the_wal_and_shm_siblings_are_not_world_readable_either(self):
        """The easy one to miss: the write-ahead log holds task content.

        SQLite creates the siblings with the process umask, which on an ordinary
        account is 0644. The directory is 0700 so nothing else can reach them,
        but a mode on the file travels with the file when it is copied or
        restored and a directory permission does not.
        """
        self.create()
        for suffix in ("-wal", "-shm"):
            sibling = self.store.path.with_name(self.store.path.name + suffix)
            if not sibling.exists():
                continue
            mode = database_permissions(sibling)
            self.assertEqual(
                mode & (stat.S_IRWXG | stat.S_IRWXO), 0, str(sibling) + " is readable"
            )

    def test_the_task_directory_is_owner_only(self):
        self.create()
        mode = database_permissions(self.store.path.parent)
        self.assertEqual(mode & (stat.S_IRWXG | stat.S_IRWXO), 0)

    def test_the_schema_version_is_recorded(self):
        self.create()
        connection = sqlite3.connect(str(self.store.path))
        try:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(int(row[0]), 1)

    def test_a_newer_schema_is_refused_rather_than_migrated_backwards(self):
        self.create()
        self.store.close()
        connection = sqlite3.connect(str(self.store.path))
        try:
            connection.execute(
                "UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(task_errors.StoreUnavailable):
            TaskStore(self.config).get("task_00000000000000000000000000")


# -- 20-22: restart ----------------------------------------------------------


class RestartRecovery(TaskTestCase):
    def test_a_running_task_becomes_interrupted(self):
        """20. Never left claiming to run."""
        row = self.create(prompt="scenario: interrupt")
        self.assertEqual(row.state, STATE_RUNNING)
        service = self.restart()
        settled = service.recover_after_restart()
        self.assertEqual([task.task_id for task in settled], [row.task_id])
        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)

    def test_nothing_is_resumed(self):
        """20. The adapter is not called, and does not learn the task exists."""
        row = self.create(prompt="scenario: interrupt")
        service = self.restart()
        service.recover_after_restart()
        adapter = self.adapters.find("validation")
        self.assertFalse(adapter.knows(row.task_id))

    def test_terminal_tasks_are_untouched(self):
        """21."""
        done = self.create()
        self.assertEqual(done.state, STATE_COMPLETED)
        before = self.store.get(done.task_id)
        service = self.restart()
        service.recover_after_restart()
        after = self.store.get(done.task_id)
        self.assertEqual(after.state, STATE_COMPLETED)
        self.assertEqual(after.lifecycle_revision, before.lifecycle_revision)
        self.assertEqual(after.completed_at, before.completed_at)
        self.assertEqual(after.final_result, before.final_result)

    def test_an_interruption_event_is_recorded(self):
        """22."""
        row = self.create(prompt="scenario: interrupt")
        service = self.restart()
        service.recover_after_restart()
        events = self.store.events(row.task_id, limit=200)
        interruption = [e for e in events if e.event_type == "task_interrupted"]
        self.assertEqual(len(interruption), 1)
        self.assertEqual(interruption[0].source, "restart_recovery")
        self.assertIn("restarted", (interruption[0].text or "").lower())

    def test_previous_output_is_preserved(self):
        """20. Interruption is not amnesia."""
        row = self.create(prompt="scenario: interrupt")
        before = self.store.get(row.task_id).latest_output
        self.assertTrue(before)
        service = self.restart()
        service.recover_after_restart()
        self.assertEqual(self.store.get(row.task_id).latest_output, before)

    def test_recovery_is_idempotent(self):
        row = self.create(prompt="scenario: interrupt")
        service = self.restart()
        service.recover_after_restart()
        revision = self.store.get(row.task_id).lifecycle_revision
        # A second pass finds nothing non-terminal to settle.
        self.assertEqual(service.recover_after_restart(), [])
        self.assertEqual(self.store.get(row.task_id).lifecycle_revision, revision)

    def test_a_waiting_task_is_also_interrupted(self):
        """A task waiting for a person is still gone when the daemon restarts."""
        row = self.create(prompt="scenario: wait")
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        service = self.restart()
        service.recover_after_restart()
        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)


# -- 23-29: content and privacy ----------------------------------------------


class TaskContent(TaskTestCase):
    def test_turkish_and_unicode_survive_a_round_trip(self):
        """23."""
        for prompt in (
            TURKISH_PROMPT,
            "Iğdır'da ışıklar açık mı?",
            "日本語のテキスト",
            "emoji 🎧 and combining é",
        ):
            row = self.create(prompt=prompt)
            self.assertEqual(self.store.get(row.task_id).prompt, prompt)

    def test_an_empty_prompt_is_rejected(self):
        """24."""
        for empty in ("", "   ", "\n\n", "\t"):
            with self.assertRaises(task_errors.PromptInvalid):
                self.create(prompt=empty)

    def test_an_oversized_prompt_is_rejected(self):
        """25. Refused, never truncated."""
        with self.assertRaises(task_errors.PromptInvalid):
            self.create(prompt="a" * (MAX_PROMPT_CHARS + 1))
        # And the boundary itself is accepted.
        self.assertEqual(
            len(self.create(prompt="a" * MAX_PROMPT_CHARS).prompt), MAX_PROMPT_CHARS
        )

    def test_control_characters_are_rejected(self):
        """26. Tab and newline are prose; the rest are not."""
        for hostile in ("bad\x00null", "bell\x07", "esc\x1b[2J", "del\x7f"):
            with self.assertRaises(task_errors.PromptInvalid):
                self.create(prompt=hostile)
        self.assertTrue(valid_user_text("line one\nline two\ttabbed", 100))

    def test_a_non_string_prompt_is_rejected(self):
        for hostile in (None, 7, True, ["a"], {"a": 1}):
            with self.assertRaises(task_errors.PromptInvalid):
                self.create(prompt=hostile)

    def test_the_prompt_never_reaches_the_audit_record(self):
        """27, 56."""
        row = self.create(prompt=TURKISH_PROMPT)
        blob = self.audit_blob()
        self.assertNotIn(TURKISH_PROMPT, blob)
        self.assertNotIn(TURKISH_PROMPT[:20], blob)
        # The ids that *are* recorded carry nothing about the content.
        self.assertIn(row.task_id, blob)
        self.assertTrue(self.audit)

    def test_the_followup_never_reaches_the_audit_record(self):
        """28."""
        secret = "gizli takip mesajı 12345"
        row = self.create(prompt="scenario: wait")
        self.service.send_followup(row.task_id, secret)
        self.assertNotIn(secret, self.audit_blob())

    def test_the_final_result_never_reaches_the_audit_record(self):
        """29."""
        row = self.create()
        result = self.store.get(row.task_id).final_result
        self.assertTrue(result)
        for line in result.splitlines():
            if len(line) > 20:
                self.assertNotIn(line, self.audit_blob())

    def test_the_audit_signature_has_no_content_parameter(self):
        """56. Structural: content cannot reach the audit path by accident."""
        import inspect

        from cofferdam.workstation.store import ActionStore

        parameters = set(inspect.signature(ActionStore.record_task_event).parameters)
        self.assertEqual(
            parameters,
            {"self", "operation", "result", "task_id", "adapter_id", "project_id",
             "correlation_id"},
        )

    def test_the_prompt_is_not_copied_into_the_event_stream(self):
        """27. Stored once, on the task."""
        row = self.create(prompt=TURKISH_PROMPT)
        for event in self.store.events(row.task_id, limit=200):
            self.assertNotIn(TURKISH_PROMPT, (event.text or "") + (event.detail or ""))

    def test_the_followup_is_not_copied_into_the_event_stream(self):
        secret = "bu metin olay akışında görünmemeli"
        row = self.create(prompt="scenario: wait")
        self.service.send_followup(row.task_id, secret)
        for event in self.store.events(row.task_id, limit=200):
            self.assertNotIn(secret, (event.text or "") + (event.detail or ""))

    def test_no_task_module_writes_content_to_a_log(self):
        """27-29. Nothing in Task Core logs at all."""
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            source = python_code_only(path.read_text("utf-8"))
            for forbidden in ("logging.", "logger.", "print(", "sys.stdout", "sys.stderr"):
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)


# -- 30-33: idempotency ------------------------------------------------------


class Idempotency(TaskTestCase):
    def test_the_same_key_returns_the_existing_task(self):
        """30, 33."""
        first, created_first = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt=TURKISH_PROMPT,
            client_request_id="tap-1",
        )
        second, created_second = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt=TURKISH_PROMPT,
            client_request_id="tap-1",
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_the_same_key_with_a_different_payload_is_refused(self):
        """31."""
        self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="first",
            client_request_id="tap-2",
        )
        with self.assertRaises(task_errors.IdempotencyConflict):
            self.service.create_task(
                project_id=PROJECT_ID,
                adapter_id="validation",
                prompt="second",
                client_request_id="tap-2",
            )

    def test_a_duplicate_creation_does_not_restart_the_adapter(self):
        """33. One task, and one start."""
        scripted = self.install_adapter(ScriptedAdapter())
        for _ in range(3):
            self.service.create_task(
                project_id=PROJECT_ID,
                adapter_id="scripted",
                prompt="only once",
                client_request_id="tap-3",
            )
        self.assertEqual(len(scripted.contexts), 1)

    def test_followup_idempotency(self):
        """32. The same answer is delivered once."""
        row = self.create(prompt="scenario: wait")
        self.service.send_followup(row.task_id, "evet", client_request_id="f-1")
        after_first = self.store.get(row.task_id)
        # The second call finds the key and returns without delivering again.
        again = self.service.send_followup(row.task_id, "evet", client_request_id="f-1")
        self.assertEqual(again.lifecycle_revision, after_first.lifecycle_revision)

    def test_an_invalid_request_id_is_refused(self):
        for hostile in ("", "   ", "a" * 200, "with\x00null", 7, True):
            with self.assertRaises(task_errors.RequestIdInvalid):
                self.service.create_task(
                    project_id=PROJECT_ID,
                    adapter_id="validation",
                    prompt="x",
                    client_request_id=hostile,
                )

    def test_a_request_id_is_not_a_task_id(self):
        """The key is a lookup, never authority over identity."""
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="x",
            client_request_id="task_zzzzzzzzzzzzzzzzzzzzzzzzzz",
        )
        self.assertNotEqual(row.task_id, "task_zzzzzzzzzzzzzzzzzzzzzzzzzz")
        self.assertTrue(valid_task_id(row.task_id))


# -- 34-40: cancellation and follow-up ---------------------------------------


class Cancellation(TaskTestCase):
    def test_a_running_task_can_be_cancelled(self):
        """34, 44."""
        row = self.create(prompt="scenario: cancel")
        self.assertEqual(row.state, STATE_RUNNING)
        cancelled = self.service.cancel_task(row.task_id)
        self.assertEqual(cancelled.state, STATE_CANCELLED)
        self.assertIn("cancellation_requested", self.event_types(row.task_id))
        self.assertIn("task_cancelled", self.event_types(row.task_id))

    def test_repeated_cancellation_does_not_corrupt_state(self):
        """35."""
        row = self.create(prompt="scenario: cancel")
        self.service.cancel_task(row.task_id)
        for _ in range(3):
            with self.assertRaises(task_errors.TaskAlreadyFinished):
                self.service.cancel_task(row.task_id)
        final = self.store.get(row.task_id)
        self.assertEqual(final.state, STATE_CANCELLED)

    def test_cancelling_one_task_leaves_another_alone(self):
        """36."""
        first = self.create(prompt="scenario: cancel")
        second = self.create(prompt="scenario: cancel")
        self.service.cancel_task(first.task_id)
        self.assertEqual(self.store.get(first.task_id).state, STATE_CANCELLED)
        self.assertEqual(self.store.get(second.task_id).state, STATE_RUNNING)

    def test_cancel_uses_the_adapter_contract(self):
        """37. It is a message to one adapter about one task."""
        scripted = self.install_adapter(ScriptedAdapter())
        row = self.create(adapter_id="scripted")
        self.service.cancel_task(row.task_id)
        self.assertEqual(scripted.cancelled, [row.task_id])

    def test_no_task_module_signals_or_enumerates_processes(self):
        """37. Structural: there is no process-killing vocabulary in Task Core.

        One directory is excepted, and only because owning a process is its
        entire job. An adapter that launches a program has to be able to stop
        it; a rule forbidding that here would not make anything safer, it would
        push the process handling somewhere this scan does not look.

        What replaces the rule for that directory is stricter than what it
        gives up, and it lives in ``tests/test_claude_code_adapter.py``: the
        adapter may signal, but only a process group it created itself, only
        after pid *and* start time *and* group ownership have been re-verified,
        and never by matching a process name. This scan could not have
        expressed any of that.
        """
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            if "claude_code" in path.parts:
                continue
            source = python_code_only(path.read_text("utf-8"))
            for forbidden in (
                "pkill",
                "killall",
                "os.kill",
                "signal.",
                "SIGTERM",
                "SIGKILL",
                "terminate()",
                "psutil",
                "subprocess",
            ):
                self.assertNotIn(forbidden, source, str(path) + " uses " + forbidden)

    def test_an_adapter_that_cannot_cancel_produces_a_refusal(self):
        scripted = self.install_adapter(ScriptedAdapter(
            adapter_id="nocancel",
            capabilities=AdapterCapabilities(start=True, followup=False, cancel=False),
        ))
        row = self.create(adapter_id="nocancel")
        with self.assertRaises(task_errors.CancelUnsupported):
            self.service.cancel_task(row.task_id)

    def test_a_refused_cancel_does_not_claim_the_task_stopped(self):
        """The false success this design refuses to produce."""
        scripted = ScriptedAdapter(adapter_id="stubborn", refuse_cancel=True)
        self.service._adapters._adapters["stubborn"] = scripted
        self.write_projects(
            [
                {
                    "project_id": PROJECT_ID,
                    "root": str(self.project_root),
                    "adapters": ["stubborn"],
                }
            ]
        )
        self.service.reload_projects()
        row = self.create(adapter_id="stubborn")
        result = self.service.cancel_task(row.task_id)
        self.assertEqual(result.state, "cancelling")
        self.assertNotEqual(result.state, STATE_CANCELLED)
        self.assertIn("action_rejected", self.event_types(row.task_id))


class Followups(TaskTestCase):
    def test_a_waiting_task_accepts_one(self):
        """38, 42."""
        row = self.create(prompt="scenario: wait")
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        self.assertEqual(row.waiting_reason, "clarification")
        after = self.service.send_followup(row.task_id, "evet devam et")
        self.assertEqual(after.state, STATE_COMPLETED)
        self.assertIn("followup_received", self.event_types(row.task_id))

    def test_a_terminal_task_refuses_one(self):
        """39."""
        row = self.create()
        with self.assertRaises(task_errors.TaskAlreadyFinished):
            self.service.send_followup(row.task_id, "too late")

    def test_a_running_task_refuses_one(self):
        row = self.create(prompt="scenario: cancel")
        with self.assertRaises(task_errors.FollowupNotWaiting):
            self.service.send_followup(row.task_id, "not waiting")

    def test_an_adapter_without_the_capability_refuses_one(self):
        """40."""
        scripted = self.install_adapter(ScriptedAdapter(
            adapter_id="nofollow",
            capabilities=AdapterCapabilities(start=True, followup=False, cancel=True),
            start_outcome=AdapterOutcome(
                requested_state=STATE_WAITING_FOR_USER, waiting_reason="clarification"
            ),
        ))
        row = self.create(adapter_id="nofollow")
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        with self.assertRaises(task_errors.FollowupUnsupported):
            self.service.send_followup(row.task_id, "hello")

    def test_a_followup_alone_does_not_complete_a_task(self):
        """15. The graph has no waiting_for_user → completed edge."""
        self.assertFalse(can_transition(STATE_WAITING_FOR_USER, STATE_COMPLETED))

    def test_an_empty_or_oversized_followup_is_refused(self):
        row = self.create(prompt="scenario: wait")
        for hostile in ("", "   ", "a" * 5000, "bad\x00", None, 7):
            with self.assertRaises(task_errors.FollowupInvalid):
                self.service.send_followup(row.task_id, hostile)


# -- 41-45: the validation scenarios end to end ------------------------------


class ValidationScenarios(TaskTestCase):
    def test_complete(self):
        """41."""
        row = self.create(prompt="scenario: complete")
        self.assertEqual(row.state, STATE_COMPLETED)
        self.assertTrue(row.final_result)
        types = self.event_types(row.task_id)
        self.assertEqual(types[0], "task_created")
        self.assertEqual(types[-1], "task_completed")
        self.assertIn("meaningful_output", types)

    def test_wait_then_resume(self):
        """42."""
        row = self.create(prompt="scenario: wait")
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)
        resumed = self.service.send_followup(row.task_id, "devam")
        self.assertEqual(resumed.state, STATE_COMPLETED)
        self.assertIn("follow-up", (resumed.final_result or "").lower())

    def test_fail(self):
        """43."""
        row = self.create(prompt="scenario: fail")
        self.assertEqual(row.state, STATE_FAILED)
        self.assertIsNotNone(row.failure)
        self.assertEqual(row.failure.code, "validation_scenario_failed")

    def test_cancel(self):
        """44."""
        row = self.create(prompt="scenario: cancel")
        self.assertEqual(self.service.cancel_task(row.task_id).state, STATE_CANCELLED)

    def test_restart_does_not_leave_it_running(self):
        """45."""
        row = self.create(prompt="scenario: interrupt")
        self.restart().recover_after_restart()
        self.assertNotEqual(self.store.get(row.task_id).state, STATE_RUNNING)
        self.assertEqual(self.store.get(row.task_id).state, STATE_INTERRUPTED)

    def test_failed_and_interrupted_are_different_states(self):
        """13 of the UX contract: they read differently because they are."""
        failed = self.create(prompt="scenario: fail")
        running = self.create(prompt="scenario: interrupt")
        self.restart().recover_after_restart()
        self.assertEqual(self.store.get(failed.task_id).state, STATE_FAILED)
        self.assertEqual(self.store.get(running.task_id).state, STATE_INTERRUPTED)


# -- 46, 54-55: listing and evidence -----------------------------------------


class Listing(TaskTestCase):
    def test_buckets_filter_correctly(self):
        """46."""
        done = self.create(prompt="scenario: complete")
        waiting = self.create(prompt="scenario: wait")
        running = self.create(prompt="scenario: cancel")

        active = [t.task_id for t in self.service.list_tasks(bucket="active")]
        self.assertIn(running.task_id, active)
        self.assertNotIn(done.task_id, active)
        self.assertNotIn(waiting.task_id, active)

        pending = [t.task_id for t in self.service.list_tasks(bucket="waiting")]
        self.assertEqual(pending, [waiting.task_id])

        finished = [t.task_id for t in self.service.list_tasks(bucket="finished")]
        self.assertIn(done.task_id, finished)
        self.assertNotIn(running.task_id, finished)

    def test_the_list_is_bounded(self):
        for _ in range(5):
            self.create()
        self.assertLessEqual(len(self.service.list_tasks(limit=10**6)), 100)
        self.assertEqual(len(self.service.list_tasks(limit=2)), 2)

    def test_the_list_snapshot_omits_content(self):
        row = self.create()
        payload = self.service.snapshot(row).to_dict(include_content=False)
        self.assertNotIn("final_result", payload)
        self.assertNotIn("latest_meaningful_output", payload)
        self.assertNotIn("prompt", payload)


class Evidence(TaskTestCase):
    def test_evidence_is_bounded(self):
        """54."""
        from cofferdam.workstation.tasks.models import EvidenceReference

        row = self.create(prompt="scenario: cancel")
        self.store.append_event(
            row.task_id,
            "progress",
            actor="adapter",
            source="adapter",
            text="lots of evidence",
            evidence=tuple(
                EvidenceReference(
                    evidence_type="file",
                    source="adapter_reported",
                    identifier="x" * 5000,
                )
                for _ in range(50)
            ),
        )
        event = self.store.events(row.task_id, limit=200)[-1]
        self.assertLessEqual(len(event.evidence), 8)
        self.assertLessEqual(len(event.evidence[0].identifier), 200)

    def test_adapter_reported_evidence_is_labelled_as_such(self):
        """55. An adapter cannot promote its own claim to an observation."""
        from cofferdam.workstation.tasks.models import EvidenceReference

        scripted = self.install_adapter(ScriptedAdapter(
            adapter_id="claimer",
            start_outcome=AdapterOutcome(
                events=(
                    AdapterEvent(
                        text="I definitely made a commit",
                        evidence=(
                            EvidenceReference(
                                evidence_type="commit",
                                # The lie: the adapter claims Cofferdam observed it.
                                source="git_observed",
                                identifier="deadbeef",
                            ),
                        ),
                    ),
                ),
                requested_state=STATE_RUNNING,
            ),
        ))
        row = self.create(adapter_id="claimer")
        events = [e for e in self.store.events(row.task_id, limit=200) if e.evidence]
        self.assertTrue(events)
        for event in events:
            for reference in event.evidence:
                self.assertEqual(reference.source, "adapter_reported")
                self.assertFalse(reference.verified)


# -- adapter misbehaviour ----------------------------------------------------


class AdapterMisbehaviour(TaskTestCase):
    def test_an_adapter_cannot_request_an_illegal_state(self):
        """An adapter does not get to move a task off the graph."""
        scripted = ScriptedAdapter(
            adapter_id="liar",
            start_outcome=AdapterOutcome(
                events=(AdapterEvent(text="going somewhere it cannot"),),
                # `starting` → `completed` is not an edge; the core refuses it
                # and the task lands in `running` from the core's own transition.
                requested_state="cancelled",
            ),
        )
        self.install_adapter(scripted)
        row = self.create(adapter_id="liar")
        self.assertNotEqual(row.state, STATE_CANCELLED)
        self.assertEqual(row.state, STATE_RUNNING)

    def test_an_adapter_cannot_write_a_lifecycle_event(self):
        """A completion in the history must come from a real transition."""
        scripted = ScriptedAdapter(
            adapter_id="forger",
            start_outcome=AdapterOutcome(
                events=(AdapterEvent(event_type="task_completed", text="I am done"),),
                requested_state=STATE_RUNNING,
            ),
        )
        self.install_adapter(scripted)
        row = self.create(adapter_id="forger")
        types = self.event_types(row.task_id)
        self.assertEqual(row.state, STATE_RUNNING)
        # The claim survives as output; it is not recorded as a completion.
        self.assertNotIn("task_completed", types)
        self.assertIn("meaningful_output", types)

    def test_an_adapter_that_raises_fails_the_task_truthfully(self):
        scripted = ScriptedAdapter(adapter_id="broken", raise_on_start=True)
        self.install_adapter(scripted)
        row = self.create(adapter_id="broken")
        self.assertEqual(row.state, STATE_FAILED)
        self.assertEqual(row.failure.code, "task_adapter_error")
        # The exception's own text never reaches the published failure.
        self.assertNotIn("on purpose", json.dumps(row.failure.to_dict()))

    def test_an_adapters_event_text_is_bounded(self):
        scripted = ScriptedAdapter(
            adapter_id="verbose",
            start_outcome=AdapterOutcome(
                events=(AdapterEvent(text="x" * 100000),), requested_state=STATE_RUNNING
            ),
        )
        self.install_adapter(scripted)
        row = self.create(adapter_id="verbose")
        for event in self.store.events(row.task_id, limit=200):
            self.assertLessEqual(len(event.text or ""), 4000)

    def test_core_owned_event_types_are_named(self):
        self.assertIn("task_completed", CORE_OWNED_EVENT_TYPES)
        self.assertIn("task_failed", CORE_OWNED_EVENT_TYPES)
        self.assertIn("task_interrupted", CORE_OWNED_EVENT_TYPES)
        self.assertNotIn("progress", CORE_OWNED_EVENT_TYPES)
        self.assertNotIn("meaningful_output", CORE_OWNED_EVENT_TYPES)


# -- the layer boundary ------------------------------------------------------


class LayerSeparation(unittest.TestCase):
    def test_task_core_names_no_specific_agent(self):
        """The permanent architecture rule, asserted rather than trusted.

        Claude-specific names, process parsing and CLI behaviour belong in an
        adapter. Task Core is scanned as code, so the documentation may discuss
        the boundary while the code may not cross it.
        """
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            if "adapters" in path.parts:
                continue  # adapters are where integration names belong
            source = python_code_only(path.read_text("utf-8")).lower()
            # "cursor" is deliberately absent from this list. It is the ordinary
            # word for a database cursor and for a paging position, and Task Core
            # uses it as both — so a scan for it would report a false positive
            # forever, and the usual repair for a noisy guard is to delete the
            # guard. The substantive property is asserted below instead: no
            # adapter for any such product is registered here.
            for forbidden in ("claude", "anthropic", "chatgpt", "openai", "codex"):
                self.assertIsNone(
                    re.search(r"\b" + forbidden + r"\b", source),
                    str(path) + " names " + forbidden,
                )

    def test_task_core_imports_no_agent_specific_module(self):
        """The boundary that survives the registry naming an agent.

        ``adapters/__init__.py`` has to name Claude Code — it is a code-owned
        table, and a table with no names in it is not a table. So the name scan
        above stops at ``adapters/``, and this is the property that keeps the
        architecture real: nothing in Task Core *outside* that directory may
        import from an agent-specific package.

        That is the rule the milestone actually cares about. A name in a
        registry is bookkeeping; an import in ``service.py`` would mean the
        core had grown a dependency on one vendor's process handling, which is
        the thing the adapter boundary exists to prevent.
        """
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            if path.parent.name == "adapters" or "claude_code" in path.parts:
                continue
            source = python_code_only(path.read_text("utf-8"))
            self.assertNotIn("claude_code", source, str(path) + " imports claude_code")

    def test_task_core_registers_no_agent_specific_adapter(self):
        """The other half: the code-owned table names no integration either.

        This is what the scan above is really protecting. An adapter for a
        specific product is legitimate — it is the *next* milestone — but it
        belongs in ``tasks/adapters/``, registered by ``build_registry``, and not
        in the core.
        """
        for registry in (build_registry(), build_registry(enable_validation_adapter=True)):
            for adapter_id in registry.ids():
                self.assertNotIn(
                    adapter_id,
                    ("claude", "claude-code", "cursor", "chatgpt", "codex", "openai"),
                )

    def test_task_core_makes_no_model_call(self):
        """Manual-first: there is no client, no key, no request to a model."""
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.rglob("*.py")):
            source = python_code_only(path.read_text("utf-8")).lower()
            for forbidden in ("api_key", "openai", "completion(", "chat.completions"):
                self.assertNotIn(forbidden, source, str(path))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
