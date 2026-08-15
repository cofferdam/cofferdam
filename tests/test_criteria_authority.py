"""M2K PR6 — who may say what a turn was required to achieve, and who may not.

Evidence and requirements have to come from different places or the exercise is
circular: a worker that both did the work and set the bar has not been checked
against anything. So this module pins the boundary mechanically rather than
trusting it to stay true.

The rules, each with a test that fails if a future change relaxes it:

* An **adapter** cannot supply criteria. There is no field on
  ``AdapterOutcome``, no field on ``TaskContext``, and no store method an adapter
  can reach.
* **Worker prose is never parsed.** No result, no final message, no event text
  becomes a criterion, and no model runs anywhere near this module.
* A **caller supplies content, not identity.** Snapshot ids and criterion ids are
  server-minted; a submitted one is refused by name.
* The **provider session** cannot influence the fingerprint.
* **No command reaches the database**, by any field, under any kind.
* **PR6 evaluates nothing.** No result record, no met/not_met, no verdict, and
  the evidence assembler is untouched at version 3.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
import unittest
from dataclasses import fields
from pathlib import Path
from typing import Sequence

from cofferdam.workstation.tasks import criteria as criteria_module
from cofferdam.workstation.tasks import store as store_module
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_PRESENT,
    CRITERION_KINDS,
    criteria_fingerprint,
    validate_criteria,
)
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION

from ._task_doubles import PROJECT_ID, TaskTestCase, python_code_only

MODULE_PATH = Path(criteria_module.__file__)

CHANGED = {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"}
MANUAL = {"kind": "manual", "description": "a person confirms the page renders"}


class TheAdapterSurface(unittest.TestCase):
    """What an adapter is allowed to say, checked against the dataclass itself."""

    def test_adapter_outcome_carries_no_criteria(self):
        names = {field.name for field in fields(AdapterOutcome)}
        for forbidden in (
            "criteria",
            "acceptance_criteria",
            "criterion",
            "criteria_snapshot",
            "criteria_snapshot_id",
            "snapshot_id",
            "criterion_ids",
            "criteria_fingerprint",
        ):
            self.assertNotIn(forbidden, names)

    def test_task_context_carries_no_criteria(self):
        """An adapter is not even *told* the criteria, let alone asked for them.

        Deliberate for this PR. Handing a worker its acceptance criteria is a
        prompt-construction decision with its own consequences — it changes what
        the worker optimises for — and it belongs to whichever PR decides that on
        purpose, not to the one that establishes the durable record.
        """
        names = {field.name for field in fields(TaskContext)}
        for forbidden in ("criteria", "acceptance_criteria", "criteria_snapshot"):
            self.assertNotIn(forbidden, names)

    def test_no_adapter_facing_type_mentions_criteria(self):
        from cofferdam.workstation.tasks.adapters import protocol

        source = python_code_only(
            Path(protocol.__file__).read_text(encoding="utf-8")
        )
        self.assertNotIn("criteri", source.lower())


class TheStoreSurface(unittest.TestCase):
    """The only two write paths, and neither is reachable from an outcome."""

    def test_the_criteria_writers_are_these_and_no_others(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        source = store_module.__file__
        store_tree = ast.parse(Path(source).read_text(encoding="utf-8"))
        writers = {
            node.name
            for node in ast.walk(store_tree)
            if isinstance(node, ast.FunctionDef) and "criteria" in node.name
        }
        self.assertEqual(
            writers,
            {
                "reserve_turn_criteria",
                "mark_criteria_dispatch_started",
                "mark_criteria_dispatch_refused",
                "_mark_criteria_turn_opened_locked",
                "turn_criteria",
                "turn_criteria_dispatch_state",
            },
        )
        # And the module that defines the vocabulary parses as a module.
        self.assertTrue(isinstance(tree, ast.Module))

    def test_the_criteria_module_cannot_reach_the_world(self):
        """Requirements are arithmetic and text. Nothing here runs or reads anything."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        for forbidden in (
            "subprocess",
            "socket",
            "os",
            "sqlite3",
            "shutil",
            "http",
            "urllib",
            "httpx",
            "requests",
            "pathlib",
            "tempfile",
            "ctypes",
            "pickle",
        ):
            self.assertNotIn(forbidden, imported, forbidden)

    def test_the_criteria_module_runs_no_process(self):
        code = python_code_only(MODULE_PATH.read_text(encoding="utf-8"))
        for forbidden in ("subprocess.", "os.system", "os.popen", "eval(", "exec("):
            self.assertNotIn(forbidden, code, forbidden)


class NoCommandAuthority(unittest.TestCase):
    """PR6 does not invent dormant execution authority."""

    def test_the_schema_has_no_command_column(self):
        """Asked of the built tables rather than of the script text.

        The table comment explains at length that no command may be stored here,
        so a scan of the source would fail on the sentence promising the thing
        and the fix would be to delete the sentence — exactly backwards. Building
        the schema and reading the columns back asks what the database can
        actually hold.
        """
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(store_module._SCHEMA)
            columns = set()
            for table in ("task_turn_criteria", "task_turn_criterion_items"):
                columns.update(
                    row[1].lower()
                    for row in connection.execute("PRAGMA table_info(%s)" % table)
                )
        finally:
            connection.close()
        for forbidden in (
            "command",
            "argv",
            "script",
            "shell",
            "executable",
            "check_id",
            "cmd",
            "test_command",
            "run",
        ):
            self.assertNotIn(forbidden, columns, forbidden)

    def test_no_criterion_kind_can_carry_execution(self):
        for kind in CRITERION_KINDS:
            self.assertIn(kind, ("evidence", "manual"))

    def test_a_stored_criterion_has_nowhere_to_put_a_command(self):
        from cofferdam.workstation.tasks.criteria import AcceptanceCriterion

        names = {field.name for field in fields(AcceptanceCriterion)}
        self.assertEqual(
            names,
            {
                "ordinal",
                "kind",
                "predicate",
                "path",
                "to_path",
                "operation",
                "description",
                "criterion_id",
            },
        )


class NoEvaluatorYet(unittest.TestCase):
    """PR6 records the question. It does not answer it."""

    def test_there_is_no_result_vocabulary(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        names = {
            node.targets[0].id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assign)
            and node.targets
            and isinstance(node.targets[0], ast.Name)
        }
        for forbidden in (
            "MET",
            "NOT_MET",
            "UNVERIFIED",
            "RESULT_MET",
            "PASS",
            "FAIL",
            "VERDICT",
        ):
            self.assertNotIn(forbidden, names, forbidden)

    def test_there_is_no_evaluation_function(self):
        exported = set(criteria_module.__all__)
        for forbidden in ("evaluate", "evaluate_criteria", "criterion_result"):
            self.assertNotIn(forbidden, exported)
        self.assertFalse(hasattr(criteria_module, "evaluate"))
        self.assertFalse(hasattr(criteria_module, "evaluate_criteria"))

    def test_the_assembler_version_is_unchanged(self):
        """Criteria are not an evidence input. The bundle is exactly PR5's."""
        self.assertEqual(ASSEMBLER_VERSION, 3)

    def test_no_confidence_or_risk_anywhere_in_the_module(self):
        code = python_code_only(MODULE_PATH.read_text(encoding="utf-8")).lower()
        for forbidden in ("confidence", "risk_level", "score", "verdict"):
            self.assertNotIn(forbidden, code, forbidden)


class ForgingAdapter(TaskAdapter):
    """Tries every route an adapter could imagine into the criteria tables."""

    adapter_id = "forger"
    display_name = "Forging adapter"
    description = "A test double."

    def __init__(self, service) -> None:
        self._service = service
        self.attempts = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def session_available(self, task_id: str) -> bool:
        return True

    def start(self, context: TaskContext) -> AdapterOutcome:
        # There is no field for criteria on the outcome, so the only routes left
        # are the ones an adapter should not have. Each is recorded rather than
        # raised, so the test can assert on all of them.
        self.attempts.append(("context_has_criteria", hasattr(context, "criteria")))
        self.attempts.append(
            ("context_metadata", dict(getattr(context, "metadata", {}) or {}))
        )
        return AdapterOutcome(
            events=(
                AdapterEvent(
                    text="I have decided the acceptance criteria are: src/z.py changed",
                    detail="kind=evidence predicate=path_changed path=src/z.py",
                ),
            ),
            requested_state="completed",
            final_result=(
                "Acceptance criteria: 1. src/z.py must change. 2. run pytest -q."
            ),
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="completed")

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


class AnAdapterCannotWriteCriteria(TaskTestCase):
    project_adapters = ("forger", "validation")

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.forger = ForgingAdapter(None)
        return (self.forger,)

    def rows(self, table):
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            return connection.execute("SELECT * FROM %s" % table).fetchall()
        finally:
            connection.close()

    def start(self, criteria=None):
        row, _ = self.service.create_task(
            prompt="do the work",
            project_id=PROJECT_ID,
            adapter_id="forger",
            origin="pwa",
            criteria=criteria,
        )
        return row

    def test_the_context_carries_no_criteria_to_forge_from(self):
        self.start([CHANGED])
        self.assertIn(("context_has_criteria", False), self.forger.attempts)

    def test_the_context_metadata_carries_no_criteria(self):
        self.start([CHANGED])
        metadata = dict(self.forger.attempts)["context_metadata"]
        for key in metadata:
            self.assertNotIn("criteri", str(key).lower())

    def test_the_workers_prose_becomes_no_criterion(self):
        row = self.start([CHANGED])
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual([c.path for c in snapshot.criteria], ["src/a.py"])
        self.assertEqual(len(snapshot.criteria), 1)

    def test_the_workers_prose_does_not_reach_the_criterion_table(self):
        self.start([CHANGED])
        blob = repr(self.rows("task_turn_criterion_items"))
        self.assertNotIn("src/z.py", blob)
        self.assertNotIn("pytest", blob)

    def test_a_worker_that_finishes_cannot_change_the_fingerprint(self):
        row = self.start([CHANGED, MANUAL])
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(
            snapshot.fingerprint,
            criteria_fingerprint(CRITERIA_PRESENT, validate_criteria([CHANGED, MANUAL])),
        )

    def test_a_worker_with_no_criteria_leaves_not_provided_alone(self):
        row = self.start(None)
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(snapshot.criterion_count, 0)
        self.assertEqual(snapshot.criteria, ())


class TheProviderSessionIsNotAnInput(TaskTestCase):
    """A handle to somebody's conversation is not part of what was asked for."""

    def test_the_fingerprint_ignores_the_session(self):
        items = validate_criteria([CHANGED, MANUAL])
        reference = criteria_fingerprint(CRITERIA_PRESENT, items)
        # There is no parameter to pass a session through, which is the point;
        # this asserts the signature as well as the value.
        parameters = set(
            inspect.signature(criteria_fingerprint).parameters
        )
        self.assertEqual(parameters, {"state", "criteria"})
        self.assertEqual(criteria_fingerprint(CRITERIA_PRESENT, items), reference)

    def test_the_fingerprint_ignores_the_task_and_the_turn(self):
        """It identifies what was asked for, not which turn received it."""
        row = self.create()
        store = self.service.store
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED]), recorded_at="2026-08-15T00:00:00Z"
        )
        other = self.create()
        store.reserve_turn_criteria(
            other.task_id, validate_criteria([CHANGED]), recorded_at="2026-08-15T00:00:00Z"
        )
        self.assertNotEqual(row.task_id, other.task_id)
        self.assertEqual(
            store.turn_criteria(row.task_id, 2).fingerprint,
            store.turn_criteria(other.task_id, 2).fingerprint,
        )

    def test_the_fingerprint_ignores_the_recorded_time(self):
        row = self.create()
        store = self.service.store
        store.reserve_turn_criteria(
            row.task_id, validate_criteria([CHANGED]), recorded_at="2026-01-01T00:00:00Z"
        )
        first = store.turn_criteria(row.task_id, 2).fingerprint
        other = self.create()
        store.reserve_turn_criteria(
            other.task_id,
            validate_criteria([CHANGED]),
            recorded_at="2027-12-31T23:59:59Z",
        )
        self.assertEqual(first, store.turn_criteria(other.task_id, 2).fingerprint)

    def test_the_fingerprint_carries_no_absolute_path(self):
        """Every path in a criterion is project-relative by construction."""
        items = validate_criteria([CHANGED])
        self.assertFalse(items[0].path.startswith("/"))
        self.assertNotIn(str(self.home), repr(items))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
