"""M2K PR25 — what did *not* change, proved mechanically rather than promised.

Two halves.

**The negative space.** Every claim PR25 makes about its own blast radius is
checked against the source and the schema instead of against the commit message:
no migration, no new route, no GET-time observation, no change-to-state
inference, no PR7 movement, no bridge or PWA authoring semantics, and exactly one
owner of the capture. A version bump that quietly dragged a second semantic along
with it is the failure mode this file exists to catch.

**The retained production artifact.** The failed deployment's v11 database is kept
as an audit copy, and it contains the actual defective observation:

    observer_version = 1, observation_state = complete,
    deploy-smoke.txt = absent, recorded_at 16:55:43.192Z

whose file the worker created at 16:55:46.446Z. Opening a copy of it under PR25
must migrate nothing and must refuse that row as state authority. The original is
never touched — the fixture copies it first and skips when it is not present, so
CI (which has no access to it) stays green while this host proves the real case.
"""

from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.acceptance import (
    AGGREGATOR_VERSION,
    SUPPORTED_ASSESSMENT_VERSIONS,
)
from cofferdam.workstation.tasks.assessment import ASSESSMENT_API_VERSION
from cofferdam.workstation.tasks.binding import (
    CURRENT_ASSESSMENT_VERSION,
    REASON_UNSUPPORTED_OBSERVER,
    SUPPORTED_OBSERVER_VERSIONS,
)
from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
from cofferdam.workstation.tasks.finalstate import FINAL_STATE_OBSERVER_VERSION
from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_PACKAGE = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
SERVICE_SOURCE = TASKS_PACKAGE / "service.py"

#: The retained failed-deployment audit database. Never opened in place.
AUDIT_COPY = Path(
    os.environ.get(
        "COFFERDAM_PR25_AUDIT_DB",
        "/home/nrgis/cofferdam/state/service-backups"
        "/m2k-pr11-pr24-prev11-20260817-163939/tasks-v11-postdeploy-audit.sqlite3",
    )
)


# -- the version decisions ----------------------------------------------------


class VersionDecisionTests(unittest.TestCase):
    """Each number, and each number that deliberately did not move."""

    def test_the_observer_moved_to_two(self):
        self.assertEqual(2, FINAL_STATE_OBSERVER_VERSION)

    def test_the_current_assessment_moved_to_four(self):
        self.assertEqual(4, CURRENT_ASSESSMENT_VERSION)

    def test_the_aggregator_did_not_move(self):
        """The fold is unchanged, so the number that owns the fold is unchanged."""
        self.assertEqual(1, AGGREGATOR_VERSION)

    def test_the_assessment_api_did_not_move(self):
        """The HTTP shape is unchanged; only nested values moved."""
        self.assertEqual(1, ASSESSMENT_API_VERSION)

    def test_the_schema_did_not_move(self):
        self.assertEqual(11, SCHEMA_VERSION)

    def test_the_evaluator_resolver_and_criteria_model_did_not_move(self):
        """PR7 was not the defect and PR25 did not touch it."""
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)

    def test_the_compatibility_sets_are_derived_and_one_version_wide(self):
        """Nothing widened, and nothing can drift out of step with its owner."""
        self.assertEqual((FINAL_STATE_OBSERVER_VERSION,), SUPPORTED_OBSERVER_VERSIONS)
        self.assertEqual((2,), SUPPORTED_OBSERVER_VERSIONS)
        self.assertEqual((CURRENT_ASSESSMENT_VERSION,), SUPPORTED_ASSESSMENT_VERSIONS)
        self.assertEqual((4,), SUPPORTED_ASSESSMENT_VERSIONS)

    def test_no_earlier_observer_version_is_tolerated(self):
        for unsupported in (0, 1, 3, 99):
            self.assertNotIn(unsupported, SUPPORTED_OBSERVER_VERSIONS)

    def test_no_earlier_assessment_version_is_tolerated(self):
        for unsupported in (1, 2, 3, 5):
            self.assertNotIn(unsupported, SUPPORTED_ASSESSMENT_VERSIONS)


# -- one capture owner --------------------------------------------------------


class CaptureOwnershipTests(unittest.TestCase):
    """The structural half of Stop Gate 3, read off the syntax tree."""

    def setUp(self) -> None:
        self.source = SERVICE_SOURCE.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def calls_to(self, name):
        """Every enclosing function that calls ``self.<name>(...)``."""
        found = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == name
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "self"
                ):
                    found.append(node.name)
        return sorted(set(found))

    def test_the_observer_has_exactly_one_caller(self):
        """`_record_final_state` is invoked by the boundary owner and nothing else."""
        self.assertEqual(["_capture_terminal_boundary"], self.calls_to("_record_final_state"))

    def test_the_boundary_owner_is_called_only_from_the_closing_transitions(self):
        """`_apply` and `_fail` are the two methods that durably close a turn."""
        self.assertEqual(
            ["_apply", "_fail"], self.calls_to("_capture_terminal_boundary")
        )

    def test_the_dispatch_paths_no_longer_observe(self):
        """Neither `_start` nor `send_followup` may guess that work is done."""
        for dispatch in ("_start", "send_followup"):
            self.assertNotIn(dispatch, self.calls_to("_record_final_state"))
            self.assertNotIn(dispatch, self.calls_to("_capture_terminal_boundary"))

    def test_restart_recovery_does_not_observe(self):
        """The one closing path with no terminal worker result behind it."""
        self.assertNotIn("recover_after_restart", self.calls_to("_capture_terminal_boundary"))

    def test_the_store_write_is_reached_from_one_place(self):
        """`self._store.record_final_state(...)` appears in exactly one method."""
        writers = sorted(
            {
                node.name
                for node in ast.walk(self.tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "record_final_state"
                    for inner in ast.walk(node)
                )
            }
        )
        self.assertEqual(["_record_final_state"], writers)


# -- no GET-time world access -------------------------------------------------


class ReadPathTests(unittest.TestCase):
    def read_methods(self):
        tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"))
        wanted = {
            "turn_assessment",
            "turn_acceptance",
            "current_criterion_assessment",
            "turn_final_state",
            "evidence_bundle",
            "turn_evaluation",
        }
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in wanted
        ]

    def test_no_read_method_observes_or_captures(self):
        forbidden = {
            "_record_final_state",
            "_capture_terminal_boundary",
            "observe_paths",
            "record_final_state",
            "capture_baseline",
            "capture_committed_range",
        }
        for method in self.read_methods():
            for inner in ast.walk(method):
                if isinstance(inner, ast.Call):
                    attribute = getattr(inner.func, "attr", None) or getattr(
                        inner.func, "id", None
                    )
                    self.assertNotIn(
                        attribute,
                        forbidden,
                        method.name + " reached the world at read time",
                    )

    def test_the_binder_touches_nothing_outside_itself(self):
        """The pure-binder rule, unchanged by the observer version bump."""
        source = (TASKS_PACKAGE / "binding.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        for forbidden in (
            "os",
            "sqlite3",
            "subprocess",
            "socket",
            "time",
            "datetime",
            "pathlib",
            "shutil",
            "urllib",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("observe_paths", source)


# -- the separations PR25 must not blur ---------------------------------------


class SeparationTests(unittest.TestCase):
    def test_pr7_still_refuses_the_state_predicates(self):
        """`path_exists` / `path_absent` remain unsupported *turn-change* questions."""
        from cofferdam.workstation.tasks.evaluation import _PREDICATES

        self.assertNotIn("path_exists", _PREDICATES)
        self.assertNotIn("path_absent", _PREDICATES)
        self.assertEqual(
            ["path_changed", "path_operation", "rename"], sorted(_PREDICATES)
        )

    def test_the_observer_was_not_taught_a_predicate(self):
        source = (TASKS_PACKAGE / "finalstate.py").read_text(encoding="utf-8")
        constants = {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for forbidden in ("path_exists", "path_absent", "path_changed", "rename"):
            self.assertNotIn(forbidden, constants)

    def test_no_change_evidence_is_reinterpreted_as_state(self):
        source = (TASKS_PACKAGE / "binding.py").read_text(encoding="utf-8")
        self.assertIn("DOMAIN_TURN_CHANGE", source)
        self.assertIn("DOMAIN_FINAL_STATE", source)
        # The two domains are produced by two disjoint branches and neither
        # branch may fall through to the other.
        self.assertNotIn("DOMAIN_FINAL_STATE if", source)
        self.assertNotIn("or DOMAIN_TURN_CHANGE", source)


# -- no schema movement -------------------------------------------------------


class SchemaTests(unittest.TestCase):
    def test_opening_a_fresh_store_writes_schema_eleven(self):
        from cofferdam.workstation.config import load_config

        with tempfile.TemporaryDirectory() as home:
            config = load_config(Path(home))
            config.ensure_dirs()
            store = TaskStore(config)
            try:
                store.storage_health()
                connection = sqlite3.connect(str(store.path))
                try:
                    version = connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                finally:
                    connection.close()
            finally:
                store.close()
        self.assertEqual("11", version[0])

    def test_no_migration_mentions_the_observer_version(self):
        """The bump is a semantic one; nothing rewrites a stored row."""
        source = (TASKS_PACKAGE / "store.py").read_text(encoding="utf-8")
        for forbidden in (
            "SET observer_version",
            "UPDATE task_turn_final_state",
            "observer_version = 2",
            "observer_version = 1",
        ):
            self.assertNotIn(forbidden, source)


# -- the retained failed-deployment artifact ----------------------------------


@unittest.skipUnless(
    AUDIT_COPY.is_file(), "the retained failed-deployment audit database is not present"
)
class FailedDeploymentArtifactTests(unittest.TestCase):
    """The real defective row, refused. On a copy; the original is never opened.

    This is the discriminating fixture: a hand-built V1 row proves the rule, and
    this proves the rule catches the thing that actually happened.
    """

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.copy = Path(self._directory.name) / "audit-copy.sqlite3"
        shutil.copy2(AUDIT_COPY, self.copy)
        self.before = self.copy.read_bytes()

    def rows(self):
        connection = sqlite3.connect(str(self.copy))
        connection.row_factory = sqlite3.Row
        try:
            observations = [
                dict(row)
                for row in connection.execute("SELECT * FROM task_turn_final_state")
            ]
            paths = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM task_turn_final_state_paths"
                )
            ]
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()["value"]
        finally:
            connection.close()
        return observations, paths, version

    def test_the_artifact_holds_the_defect_this_pr_exists_for(self):
        observations, paths, version = self.rows()
        self.assertEqual("11", version)
        self.assertEqual(1, len(observations))
        self.assertEqual(1, observations[0]["observer_version"])
        self.assertEqual("complete", observations[0]["observation_state"])
        self.assertEqual(1, len(paths))
        self.assertEqual("deploy-smoke.txt", paths[0]["path"])
        self.assertEqual("absent", paths[0]["path_state"])

    def test_the_row_is_refused_as_state_authority(self):
        from cofferdam.workstation.tasks.finalstate import (
            FinalStateObservation,
            PathObservation,
        )

        observations, paths, _ = self.rows()
        stored = observations[0]
        observation = FinalStateObservation(
            task_id=stored["task_id"],
            turn_number=stored["turn_number"],
            state=stored["observation_state"],
            observation_id=stored["observation_id"],
            observer_version=stored["observer_version"],
            limitation_reason=stored["limitation_reason"],
            lineage_fingerprint=stored["lineage_fingerprint"],
            head_revision=stored["head_revision"],
            path_count=stored["path_count"],
            fingerprint=stored["observation_fingerprint"],
            recorded_at=stored["recorded_at"],
            paths=tuple(
                PathObservation(
                    ordinal=item["ordinal"],
                    path=item["path"],
                    state=item["path_state"],
                    kind=item["kind"],
                    reason=item["reason"],
                )
                for item in paths
            ),
        )

        # The identity check `_final_state_defect` performs first, in isolation:
        # this row's semantics are not ones this build will interpret.
        self.assertNotIn(
            int(observation.observer_version), SUPPORTED_OBSERVER_VERSIONS
        )

        from cofferdam.workstation.tasks.binding import _final_state_defect

        class _Resolved:
            task_id = observation.task_id
            target_turn_number = observation.turn_number
            fingerprint = observation.lineage_fingerprint

        self.assertEqual(
            REASON_UNSUPPORTED_OBSERVER,
            _final_state_defect(observation, _Resolved(), ("deploy-smoke.txt",)),
        )

    def test_reading_the_copy_never_altered_it(self):
        self.rows()
        self.assertEqual(self.before, self.copy.read_bytes())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
