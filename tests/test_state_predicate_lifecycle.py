"""M2K PR17 — a state predicate through the real lifecycle, before anything evaluates it.

This module answers the question that decided whether PR17 could exist at all:
**can Cofferdam safely admit `path_exists`/`path_absent` into persisted criteria
while PR7 and PR16 do not yet understand them?**

The answer is yes, and not by luck. Both layers were built *total* with an
explicit seat for a capability that does not exist yet — PR7's evaluator returns
`unverified` / `unsupported_capability` for a predicate it has no handler for,
and PR16's binder returns `unverified` / `unsupported_predicate` for anything
outside its change-predicate set. So a state criterion is stored, resolved,
inherited, superseded and observed exactly like any other, and every layer that
cannot decide it says so rather than crashing, guessing, or inventing a result.

Also pinned: PR14 contributes the criterion's path to its bounded observation
scope — a *representation* consequence — while producing no acceptance result
whatsoever. Observing that a path is present is not deciding a criterion.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    CURRENT_ASSESSMENT_VERSION,
    DOMAIN_NOT_APPLICABLE,
    DOMAIN_TURN_CHANGE,
    REASON_FINAL_STATE_NOT_RECORDED,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_UNSUPPORTED_PREDICATE,
)
from cofferdam.workstation.tasks.continuity import validate_declaration
from cofferdam.workstation.tasks.criteria import (
    CHANGE_PREDICATES,
    CRITERIA_PRESENT,
    CRITERIA_MODEL_VERSION,
    EVIDENCE_PREDICATES,
    PREDICATE_PATH_ABSENT,
    PREDICATE_PATH_EXISTS,
    STATE_PREDICATES,
    CriteriaSubmissionInvalid,
    criteria_fingerprint,
    validate_criteria,
)
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    REASON_UNSUPPORTED_CAPABILITY,
    RESULT_UNVERIFIED,
)
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    PATH_ABSENT,
    PATH_PRESENT,
    target_paths,
)
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class VocabularyTests(unittest.TestCase):
    def test_the_two_state_predicates_exist(self):
        self.assertEqual(("path_exists", "path_absent"), STATE_PREDICATES)

    def test_the_change_predicates_are_unchanged(self):
        self.assertEqual(
            ("path_changed", "path_operation", "rename"), CHANGE_PREDICATES
        )

    def test_the_two_sets_are_disjoint_and_together_are_the_vocabulary(self):
        self.assertEqual((), tuple(set(CHANGE_PREDICATES) & set(STATE_PREDICATES)))
        self.assertEqual(CHANGE_PREDICATES + STATE_PREDICATES, EVIDENCE_PREDICATES)

    def test_the_binder_still_only_binds_change_predicates(self):
        from cofferdam.workstation.tasks.binding import (
            CHANGE_PREDICATES as BOUND,
        )

        self.assertEqual(("path_changed", "path_operation", "rename"), BOUND)


class ValidationTests(unittest.TestCase):
    def state(self, predicate, **extra):
        submitted = {"kind": "evidence", "predicate": predicate, "path": "foo.py"}
        submitted.update(extra)
        return validate_criteria([submitted])

    def test_path_exists_validates(self):
        (criterion,) = self.state(PREDICATE_PATH_EXISTS)
        self.assertEqual(PREDICATE_PATH_EXISTS, criterion.predicate)
        self.assertEqual("foo.py", criterion.path)
        self.assertIsNone(criterion.operation)
        self.assertIsNone(criterion.to_path)

    def test_path_absent_validates(self):
        (criterion,) = self.state(PREDICATE_PATH_ABSENT)
        self.assertEqual(PREDICATE_PATH_ABSENT, criterion.predicate)
        self.assertIsNone(criterion.operation)
        self.assertIsNone(criterion.to_path)

    def test_a_state_predicate_needs_a_path(self):
        with self.assertRaises(CriteriaSubmissionInvalid):
            validate_criteria(
                [{"kind": "evidence", "predicate": PREDICATE_PATH_EXISTS}]
            )

    def test_a_state_predicate_refuses_an_operation(self):
        with self.assertRaises(CriteriaSubmissionInvalid):
            self.state(PREDICATE_PATH_EXISTS, operation="created")

    def test_a_state_predicate_refuses_a_destination(self):
        with self.assertRaises(CriteriaSubmissionInvalid):
            self.state(PREDICATE_PATH_ABSENT, to_path="bar.py")

    def test_the_shared_path_gate_is_reused_not_weakened(self):
        """No second, laxer path parser for state predicates."""
        for bad in ("../escape.py", "/etc/passwd", "~/secret", "a\x00b", "./x.py"):
            with self.assertRaises(CriteriaSubmissionInvalid, msg=bad):
                validate_criteria(
                    [{"kind": "evidence", "predicate": PREDICATE_PATH_EXISTS, "path": bad}]
                )

    def test_a_sensitive_path_is_refused_for_state_predicates_too(self):
        with self.assertRaises(CriteriaSubmissionInvalid):
            validate_criteria(
                [
                    {
                        "kind": "evidence",
                        "predicate": PREDICATE_PATH_EXISTS,
                        "path": ".ssh/id_rsa",
                    }
                ]
            )

    def test_an_unknown_predicate_is_still_refused(self):
        with self.assertRaises(CriteriaSubmissionInvalid):
            validate_criteria(
                [{"kind": "evidence", "predicate": "path_maybe", "path": "foo.py"}]
            )


class FingerprintTests(unittest.TestCase):
    def snapshot(self, predicate, path="foo.py"):
        return criteria_fingerprint(
            CRITERIA_PRESENT,
            validate_criteria(
                [{"kind": "evidence", "predicate": predicate, "path": path}]
            ),
        )

    def test_it_is_deterministic(self):
        self.assertEqual(
            self.snapshot(PREDICATE_PATH_EXISTS), self.snapshot(PREDICATE_PATH_EXISTS)
        )

    def test_the_predicate_moves_it(self):
        """`path_exists(foo.py)` and `path_absent(foo.py)` are different requirements."""
        self.assertNotEqual(
            self.snapshot(PREDICATE_PATH_EXISTS), self.snapshot(PREDICATE_PATH_ABSENT)
        )

    def test_the_path_moves_it(self):
        self.assertNotEqual(
            self.snapshot(PREDICATE_PATH_EXISTS),
            self.snapshot(PREDICATE_PATH_EXISTS, path="bar.py"),
        )

    def test_a_state_predicate_does_not_hash_like_the_change_one_it_resembles(self):
        """`path_operation(foo.py, created)` is not `path_exists(foo.py)`."""
        created = criteria_fingerprint(
            CRITERIA_PRESENT,
            validate_criteria(
                [
                    {
                        "kind": "evidence",
                        "predicate": "path_operation",
                        "path": "foo.py",
                        "operation": "created",
                    }
                ]
            ),
        )
        self.assertNotEqual(created, self.snapshot(PREDICATE_PATH_EXISTS))

    def test_no_new_criteria_model_version_was_needed(self):
        """The existing fingerprint already binds predicate and path honestly."""
        self.assertEqual(1, CRITERIA_MODEL_VERSION)


class LifecycleCase(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        (self.root / "README.md").write_text("a repository\n", encoding="utf-8")

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
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)

        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects

        adapters = build_registry(enable_validation_adapter=True)
        self.service = TaskService(
            config, self.store, adapters, projects=load_projects(config, adapters.ids())
        )
        self.task_id = new_task_id()
        self.store.storage_health()
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, title, prompt)"
                " VALUES (?,'cor','pwa','validation',?,'running','x','x','t','p')",
                (self.task_id, PROJECT_ID),
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

    def turn(self, specs, declaration):
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(specs), recorded_at="x"
        )
        number = self.store.reserve_turn_continuity(
            self.task_id, validate_declaration(declaration), recorded_at="x"
        )
        self.store.mark_criteria_dispatch_started(self.task_id, number)
        self.store.mark_continuity_dispatch_started(self.task_id, number)
        with self.sql() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome)"
                " VALUES (?,?,'validation','pwa','x','y','completed')",
                (self.task_id, number),
            )
            connection.execute(
                "INSERT OR IGNORE INTO task_turn_bounds (task_id, turn_number,"
                " opened_after_event_sequence, closed_through_event_sequence)"
                " VALUES (?,?,0,1)",
                (self.task_id, number),
            )
        return number

    def state_criterion(self, predicate=PREDICATE_PATH_EXISTS, path="foo.py"):
        return {"kind": "evidence", "predicate": predicate, "path": path}


class StorageAndLineageTests(LifecycleCase):
    def test_a_state_criterion_round_trips_through_the_store(self):
        number = self.turn([self.state_criterion()], {"mode": "root"})
        snapshot = self.store.turn_criteria(self.task_id, number)
        (criterion,) = snapshot.criteria
        self.assertEqual(PREDICATE_PATH_EXISTS, criterion.predicate)
        self.assertEqual("foo.py", criterion.path)
        self.assertIsNone(criterion.operation)
        self.assertIsNone(criterion.to_path)
        self.assertTrue(criterion.criterion_id)

    def test_its_fingerprint_survives_a_reopen(self):
        number = self.turn([self.state_criterion()], {"mode": "root"})
        before = self.store.turn_criteria(self.task_id, number).fingerprint
        self.store.close()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        self.assertEqual(
            before, store.turn_criteria(self.task_id, number).fingerprint
        )

    def test_it_participates_in_lineage_like_any_other_criterion(self):
        first = self.turn([self.state_criterion()], {"mode": "root"})
        snapshot = self.store.turn_criteria(self.task_id, first).snapshot_id
        second = self.turn(
            [self.state_criterion(path="bar.py")],
            {"mode": "extend", "predecessor_snapshot_id": snapshot},
        )
        resolved = self.service.resolve_active_criteria(self.task_id, second)
        self.assertTrue(resolved.resolved)
        self.assertEqual(
            ["foo.py", "bar.py"], [entry.criterion.path for entry in resolved.active]
        )

    def test_it_can_be_superseded_by_a_revise(self):
        first = self.turn([self.state_criterion()], {"mode": "root"})
        original = self.store.turn_criteria(self.task_id, first).criteria[0]
        second = self.turn(
            [self.state_criterion(PREDICATE_PATH_ABSENT, "foo.py")],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.store.turn_criteria(
                    self.task_id, first
                ).snapshot_id,
                "supersedes": [
                    {
                        "criterion_ordinal": 1,
                        "predecessor_criterion_id": original.criterion_id,
                    }
                ],
            },
        )
        resolved = self.service.resolve_active_criteria(self.task_id, second)
        self.assertEqual(
            [PREDICATE_PATH_ABSENT],
            [entry.criterion.predicate for entry in resolved.active],
        )

    def test_a_replace_cuts_it_like_any_other(self):
        first = self.turn([self.state_criterion()], {"mode": "root"})
        second = self.turn(
            [self.state_criterion(path="other.py")],
            {
                "mode": "replace",
                "predecessor_snapshot_id": self.store.turn_criteria(
                    self.task_id, first
                ).snapshot_id,
            },
        )
        resolved = self.service.resolve_active_criteria(self.task_id, second)
        self.assertEqual(
            ["other.py"], [entry.criterion.path for entry in resolved.active]
        )

    def test_a_state_predicate_does_not_supersede_a_change_one_automatically(self):
        """Lineage is declared. Nothing pairs `created` with `exists` by itself."""
        first = self.turn(
            [{"kind": "evidence", "predicate": "path_operation",
              "path": "foo.py", "operation": "created"}],
            {"mode": "root"},
        )
        second = self.turn(
            [self.state_criterion(PREDICATE_PATH_EXISTS, "foo.py")],
            {
                "mode": "extend",
                "predecessor_snapshot_id": self.store.turn_criteria(
                    self.task_id, first
                ).snapshot_id,
            },
        )
        resolved = self.service.resolve_active_criteria(self.task_id, second)
        # Both remain active. Neither replaced the other.
        self.assertEqual(2, len(resolved.active))
        self.assertEqual(
            ["path_operation", "path_exists"],
            [entry.criterion.predicate for entry in resolved.active],
        )


class StopGateFourTests(LifecycleCase):
    """Representable before evaluatable: every layer fails closed, none crashes."""

    def test_the_pr7_evaluator_answers_unverified_unsupported(self):
        number = self.turn([self.state_criterion()], {"mode": "root"})
        self.assertEqual(1, self.service.evaluate_closed_turns(self.task_id))
        record = self.store.evaluation(self.task_id, number)
        self.assertIsNotNone(record, "the turn produced no evaluation at all")
        (result,) = record.results
        self.assertEqual(RESULT_UNVERIFIED, result.result)
        self.assertEqual(REASON_UNSUPPORTED_CAPABILITY, result.reason)

    def test_the_evaluation_record_is_valid_and_complete(self):
        """No dropped criterion, no count mismatch, no invalid row."""
        number = self.turn(
            [self.state_criterion(), self.state_criterion(PREDICATE_PATH_ABSENT, "b.py")],
            {"mode": "root"},
        )
        self.service.evaluate_closed_turns(self.task_id)
        record = self.store.evaluation(self.task_id, number)
        self.assertEqual(2, record.result_count)
        self.assertEqual(2, len(record.results))
        self.assertEqual(EVALUATOR_VERSION, record.evaluator_version)

    def test_it_is_never_reported_as_not_met(self):
        number = self.turn([self.state_criterion()], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        results = self.store.evaluation(self.task_id, number).results
        self.assertNotIn("not_met", [item.result for item in results])
        self.assertNotIn("met", [item.result for item in results])

    def test_the_turn_still_closes_normally(self):
        number = self.turn([self.state_criterion()], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        with self.sql() as connection:
            row = connection.execute(
                "SELECT completed_at, outcome FROM task_turns"
                " WHERE task_id = ? AND turn_number = ?",
                (self.task_id, number),
            ).fetchone()
        self.assertIsNotNone(row["completed_at"])
        self.assertEqual("completed", row["outcome"])

    def test_the_binder_answers_from_final_state_or_says_it_has_none(self):
        """M2K PR18 replaced `unsupported_predicate` here, not the fail-closed rule.

        These turns are synthesised straight into SQLite, so no PR14 observation
        was ever recorded for them — the state criterion is therefore
        `unverified` / `final_state_not_recorded`. That is the same refusal PR17
        pinned, now stated in the vocabulary of the domain that owns it: the
        binder knows the predicate and has no evidence, rather than not knowing
        the predicate at all. It does **not** go and look.
        """
        number = self.turn([self.state_criterion()], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        answer = self.service.current_criterion_assessment(self.task_id, number)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        (assessment,) = answer.assessments
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertEqual(REASON_FINAL_STATE_NOT_RECORDED, assessment.reason)
        self.assertNotEqual(REASON_UNSUPPORTED_PREDICATE, assessment.reason)

    def test_the_binder_never_classifies_it_as_turn_change(self):
        """The failure that would silently make a state predicate a change one."""
        number = self.turn([self.state_criterion()], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        (assessment,) = self.service.current_criterion_assessment(
            self.task_id, number
        ).assessments
        self.assertNotEqual(DOMAIN_TURN_CHANGE, assessment.domain)
        self.assertEqual(DOMAIN_NOT_APPLICABLE, assessment.domain)
        self.assertIsNone(assessment.evidence_fingerprint)

    def test_an_inherited_state_criterion_is_not_carried_forward(self):
        first = self.turn([self.state_criterion()], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        second = self.turn(
            [{"kind": "evidence", "predicate": "path_changed", "path": "b.py"}],
            {
                "mode": "extend",
                "predecessor_snapshot_id": self.store.turn_criteria(
                    self.task_id, first
                ).snapshot_id,
            },
        )
        self.service.evaluate_closed_turns(self.task_id)
        answer = self.service.current_criterion_assessment(self.task_id, second)
        inherited = answer.assessments[0]
        self.assertEqual(RESULT_UNVERIFIED, inherited.result)
        # Not "inherited change", which is a statement about a question that
        # cannot be re-asked. A state question *can* be, at this very turn — so
        # the reason names the missing evidence for this turn, and turn 1's
        # answer is not reused whatever it was.
        self.assertEqual(REASON_FINAL_STATE_NOT_RECORDED, inherited.reason)
        self.assertNotEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, inherited.reason)

    def test_no_final_state_observation_is_interpreted(self):
        """PR14 observes the path. Nothing turns that into an acceptance result."""
        number = self.turn([self.state_criterion()], {"mode": "root"})
        self.service.evaluate_closed_turns(self.task_id)
        (assessment,) = self.service.current_criterion_assessment(
            self.task_id, number
        ).assessments
        self.assertEqual(RESULT_UNVERIFIED, assessment.result)
        self.assertIsNone(assessment.evidence_fingerprint)


class FinalStateScopeTests(unittest.TestCase):
    """PR14's bounded scope picks the path up — a representation consequence only."""

    def criterion(self, predicate, path):
        from cofferdam.workstation.tasks.criteria import AcceptanceCriterion

        return AcceptanceCriterion(
            ordinal=1, kind="evidence", predicate=predicate, path=path
        )

    def active(self, criterion):
        from cofferdam.workstation.tasks.lineage import ActiveCriterion

        return ActiveCriterion(
            criterion_id="criterion_0001",
            source_snapshot_id="snapshot_0001",
            source_turn_number=1,
            source_ordinal=1,
            criterion=criterion,
        )

    def test_a_state_criterion_contributes_its_path_to_the_observation_scope(self):
        for predicate in STATE_PREDICATES:
            with self.subTest(predicate=predicate):
                self.assertEqual(
                    ("foo.py",),
                    target_paths([self.active(self.criterion(predicate, "foo.py"))]),
                )

    def test_the_observer_was_not_taught_the_predicate(self):
        """It contributes a *path*. The observer never sees the predicate."""
        import inspect

        from cofferdam.workstation.tasks import finalstate

        source = inspect.getsource(finalstate)
        tree = ast.parse(source)
        names = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for forbidden in ("path_exists", "path_absent"):
            self.assertNotIn(forbidden, names)
        self.assertEqual(1, FINAL_STATE_OBSERVER_VERSION)


class NegativeSpaceTests(unittest.TestCase):
    def python_sources(self):
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            yield path, path.read_text(encoding="utf-8")

    def defined_names(self, text):
        names = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                names.add(node.attr)
        return names

    def test_the_schema_is_version_eleven(self):
        self.assertEqual(11, SCHEMA_VERSION)

    def test_no_semantic_version_moved(self):
        self.assertEqual(1, EVALUATOR_VERSION)
        # M2K PR18 moved this one (a second evidence domain) and M2K PR20 moved
        # it again (lineage-failure fidelity). The evaluator and the observer
        # below did not change in either, which is the assertion that keeps both
        # read-semantics changes.
        self.assertEqual(3, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual(1, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)
        from cofferdam.workstation.tasks.continuity import CONTINUITY_MODEL_VERSION
        from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
        from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION

        self.assertEqual(1, CONTINUITY_MODEL_VERSION)
        self.assertEqual(3, ASSEMBLER_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)

    def test_the_evaluator_has_no_handler_for_a_state_predicate(self):
        from cofferdam.workstation.tasks import evaluation

        self.assertEqual(
            {"path_changed", "path_operation", "rename"},
            set(evaluation._PREDICATES),
        )

    def test_no_state_evaluation_function_was_added(self):
        for path, text in self.python_sources():
            if path.name == "acceptance.py":
                continue  # M2K PR21; see test_the_acceptance_module_is_the_only_aggregate...
            defined = self.defined_names(text)
            for forbidden in (
                "_evaluate_path_exists",
                "_evaluate_path_absent",
                "evaluate_state",
                "evaluate_final_state",
            ):
                self.assertNotIn(forbidden, defined, "%s: %s" % (path, forbidden))

    def test_no_aggregator_version_or_aggregate_appeared(self):
        import re

        forbidden = {
            "all_met", "aggregate", "task_verdict", "acceptance_outcome",
            "CheckRunner", "run_check", "overall_result",
        }
        for path, text in self.python_sources():
            if path.name == "acceptance.py":
                continue  # M2K PR21; see test_the_acceptance_module_is_the_only_aggregate...
            self.assertEqual(
                [], re.findall(r"^\s*AGGREGATOR_VERSION\s*[:=]", text, re.M), str(path)
            )
            self.assertEqual(set(), self.defined_names(text) & forbidden, str(path))


    def test_the_acceptance_module_is_the_only_aggregate_and_is_turn_scoped(self):
        """M2K PR21 built an aggregate; this pins how far it is allowed to go.

        The scans above used to ban `AGGREGATOR_VERSION` outright. That claim has
        been overtaken rather than weakened: it now lives in exactly one module,
        and what it defines is a **target-turn** aggregate with no task verdict,
        no check runner and no lifecycle vocabulary.
        """
        import ast as _ast

        definers = set()
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            for node in _ast.walk(_ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, _ast.Assign):
                    for target in node.targets:
                        if isinstance(target, _ast.Name) and target.id == "AGGREGATOR_VERSION":
                            definers.add(path.name)
        self.assertEqual({"acceptance.py"}, definers)

        from cofferdam.workstation.tasks import acceptance

        self.assertEqual(1, acceptance.AGGREGATOR_VERSION)
        for forbidden in ("task_verdict", "task_acceptance", "overall_result",
                          "all_met", "latest_acceptance", "CheckRunner",
                          "run_check", "check_id", "project_acceptance"):
            self.assertFalse(hasattr(acceptance, forbidden))
            self.assertNotIn(forbidden, acceptance.__all__)

    def test_no_route_or_bridge_operation_was_added(self):
        surfaces = [
            REPO_ROOT / "cofferdam" / "workstation" / "service.py",
            REPO_ROOT / "cofferdam" / "workstation" / "actions.py",
        ]
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        if bridge.exists():
            surfaces.extend(sorted(bridge.rglob("*.py")))
        for path in surfaces:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for forbidden in ("path_exists", "path_absent", "current_criterion_assessment"):
                self.assertNotIn(forbidden, text, str(path))

    def test_the_pwa_was_not_taught_the_new_predicates(self):
        base = REPO_ROOT / "cofferdam" / "workstation"
        for pattern in ("*.js", "*.html", "*.css"):
            for path in base.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for forbidden in ("path_exists", "path_absent"):
                    self.assertNotIn(forbidden, text, str(path))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
