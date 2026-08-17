"""M2K PR26 — what this change deliberately does not touch.

A capture site moved. Nothing else was allowed to, and "nothing else" is asserted
here rather than left to a reviewer's memory of the diff: no schema move, no
version move, no new predicate, no new route, no PWA or bridge surface, no
GET-time capture, no second timing mechanism, and no merging of the two evidence
domains that now share a lifecycle boundary.

Mirrors ``tests/test_terminal_bound_final_state_negative_space.py``, because the
two changes have the same shape and should be falsifiable the same way.
"""

from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SOURCE = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "service.py"


# -- versions ------------------------------------------------------------------


class VersionDecisionTests(unittest.TestCase):
    """Every version that could have moved, and did not. See D-2026-08-18-2."""

    def test_the_schema_did_not_move(self):
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(11, SCHEMA_VERSION)

    def test_the_assembler_did_not_move(self):
        """Assembly reads a stored range exactly as before. The inputs moved."""
        from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION

        self.assertEqual(3, ASSEMBLER_VERSION)

    def test_the_evaluator_did_not_move(self):
        """The load-bearing refusal: PR26 changes no evaluator logic."""
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION

        self.assertEqual(1, EVALUATOR_VERSION)

    def test_the_aggregator_and_assessment_api_did_not_move(self):
        from cofferdam.workstation.tasks.acceptance import AGGREGATOR_VERSION
        from cofferdam.workstation.tasks.assessment import ASSESSMENT_API_VERSION

        self.assertEqual(1, AGGREGATOR_VERSION)
        self.assertEqual(1, ASSESSMENT_API_VERSION)

    def test_pr25_versions_are_exactly_where_pr25_left_them(self):
        """PR25's design is not reopened, in either direction."""
        from cofferdam.workstation.tasks.binding import (
            CURRENT_ASSESSMENT_VERSION,
            SUPPORTED_OBSERVER_VERSIONS,
        )
        from cofferdam.workstation.tasks.finalstate import (
            FINAL_STATE_OBSERVER_VERSION,
        )

        self.assertEqual(2, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(4, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual((2,), SUPPORTED_OBSERVER_VERSIONS)

    def test_no_committed_range_observer_version_was_introduced(self):
        """The rejected option, pinned so a later build has to argue for it.

        A version here would be load-bearing only if the evaluator read it, and
        an evaluator that reads it is an evaluator that moved — which is the bump
        rejected above. An inert one would move every new turn's fingerprint for
        nothing.
        """
        import cofferdam.workstation.tasks.gitrange as gitrange

        for name in dir(gitrange):
            self.assertNotIn(
                "OBSERVER_VERSION", name, "gitrange grew an observer version"
            )
        self.assertNotIn("observer_version", gitrange.__all__)

    def test_the_evidence_reference_gained_no_field(self):
        """The stored shape is unchanged, which is why nothing needs migrating."""
        from cofferdam.workstation.tasks.models import EvidenceReference

        self.assertEqual(
            [
                "evidence_type",
                "source",
                "identifier",
                "operation",
                "result",
                "observed_at",
                "change_kind",
                "previous_identifier",
                "change_status",
                "domain",
            ],
            list(EvidenceReference.__dataclass_fields__),
        )


# -- one owner, read off the syntax tree ---------------------------------------


class CaptureOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"))

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

    def test_the_range_observer_has_exactly_one_caller(self):
        self.assertEqual(
            ["_capture_terminal_boundary"], self.calls_to("_record_committed_range")
        )

    def test_both_observers_share_the_one_owner(self):
        """No second asynchronous timing mechanism was created."""
        self.assertEqual(
            self.calls_to("_record_committed_range"),
            self.calls_to("_record_final_state"),
        )

    def test_the_owner_is_still_called_only_from_the_closing_transitions(self):
        self.assertEqual(
            ["_apply", "_fail"], self.calls_to("_capture_terminal_boundary")
        )

    def test_the_dispatch_paths_no_longer_measure(self):
        for dispatch in ("_start", "send_followup"):
            self.assertNotIn(dispatch, self.calls_to("_record_committed_range"))
            self.assertNotIn(dispatch, self.calls_to("_capture_terminal_boundary"))

    def test_restart_recovery_does_not_measure(self):
        self.assertNotIn(
            "recover_after_restart", self.calls_to("_capture_terminal_boundary")
        )

    def test_the_order_at_the_boundary_is_pinned_in_source(self):
        """Range then final state, in the owner's body, in that order.

        Read off the syntax tree rather than from a run, so a reordering is a
        test failure even if no behaviour visibly changes — which is the point:
        both calls are read-only, so a swap would be invisible until a crash
        landed between them.
        """
        owner = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_capture_terminal_boundary"
        )
        order = [
            inner.func.attr
            for inner in ast.walk(owner)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in ("_record_committed_range", "_record_final_state")
        ]
        self.assertEqual(["_record_committed_range", "_record_final_state"], order)

    def test_the_range_event_is_appended_from_one_place(self):
        writers = sorted(
            {
                node.name
                for node in ast.walk(self.tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "append_event"
                    and any(
                        isinstance(argument, ast.Name)
                        and argument.id == "EVENT_COMMITTED_RANGE_OBSERVED"
                        for argument in inner.args
                    )
                    for inner in ast.walk(node)
                )
            }
        )
        self.assertEqual(["_record_committed_range"], writers)


# -- no capture on a read ------------------------------------------------------


class ReadPathTests(unittest.TestCase):
    def test_no_read_method_captures(self):
        """A GET may not manufacture machine evidence, at any depth."""
        tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"))
        wanted = {
            "get_task",
            "get_result",
            "list_tasks",
            "turn_evaluation",
            "turn_acceptance",
            "turn_final_state",
            "current_criterion_assessment",
            "resolve_active_criteria",
        }
        forbidden = {
            "_record_committed_range",
            "_record_final_state",
            "_capture_terminal_boundary",
            "capture_committed_range",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in wanted:
                continue
            called = {
                inner.func.attr
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
            } | {
                inner.func.id
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
            }
            self.assertEqual(
                set(), called & forbidden, node.name + " captures on a read"
            )

    def test_assembly_still_cannot_reach_the_world(self):
        """The assembler reads stored rows and runs no Git. Unchanged by PR26."""
        import cofferdam.workstation.tasks.evidence as evidence

        source = Path(evidence.__file__).read_text(encoding="utf-8")
        for banned in ("import subprocess", "from subprocess", "from .gitrange"):
            self.assertNotIn(banned, source)


# -- the vocabulary did not grow ------------------------------------------------


class VocabularyTests(unittest.TestCase):
    def test_no_new_predicate(self):
        from cofferdam.workstation.tasks.criteria import (
            CHANGE_PREDICATES,
            EVIDENCE_PREDICATES,
            STATE_PREDICATES,
        )
        from cofferdam.workstation.tasks.evaluation import _PREDICATES

        # PR7 still decides exactly the three change predicates, and PR26 taught
        # it nothing: the range is a better input to the same questions.
        self.assertEqual(
            ["path_changed", "path_operation", "rename"], sorted(_PREDICATES)
        )
        self.assertEqual(sorted(CHANGE_PREDICATES), sorted(_PREDICATES))
        self.assertEqual(
            [
                "path_absent",
                "path_changed",
                "path_exists",
                "path_operation",
                "rename",
            ],
            sorted(EVIDENCE_PREDICATES),
        )
        self.assertEqual(["path_absent", "path_exists"], sorted(STATE_PREDICATES))

    def test_no_new_range_reason_or_operation(self):
        """The range's own vocabulary is frozen; only its capture site moved."""
        from cofferdam.workstation.tasks.gitrange import (
            RANGE_CAPTURE_STATES,
            RANGE_COVERAGES,
            RANGE_OP_BASELINE,
            RANGE_OP_COVERAGE,
            RANGE_OP_LIMITATION,
            RANGE_OP_PATH,
            RANGE_OP_TARGET,
            RANGE_REASONS,
        )

        self.assertEqual(("captured", "unavailable"), RANGE_CAPTURE_STATES)
        self.assertEqual(
            ("complete", "incomplete", "unavailable"), RANGE_COVERAGES
        )
        self.assertEqual(
            [
                "git diff --name-status",
                "range baseline",
                "range coverage",
                "range limitation",
                "range target",
            ],
            sorted(
                [
                    RANGE_OP_PATH,
                    RANGE_OP_BASELINE,
                    RANGE_OP_TARGET,
                    RANGE_OP_COVERAGE,
                    RANGE_OP_LIMITATION,
                ]
            ),
        )
        self.assertEqual(
            [
                "baseline_unavailable",
                "evidence_budget_exceeded",
                "history_diverged",
                "malformed_record",
                "not_a_repository",
                "output_truncated",
                "probe_failed",
                "probe_timeout",
                "target_unstable",
                "unsafe_path_refused",
            ],
            sorted(RANGE_REASONS),
        )

    def test_the_two_domains_are_still_two(self):
        """A shared lifecycle moment is not a merged persistence model."""
        from cofferdam.workstation.tasks.models import (
            OBSERVATION_DOMAINS,
            OBSERVATION_DOMAIN_COMMITTED_RANGE,
            OBSERVATION_DOMAIN_WORKTREE,
        )

        self.assertEqual(
            sorted(
                [OBSERVATION_DOMAIN_WORKTREE, OBSERVATION_DOMAIN_COMMITTED_RANGE]
            ),
            sorted(OBSERVATION_DOMAINS),
        )

    def test_the_range_probe_argv_is_still_constant(self):
        """No caller-supplied Git argument, from the new call site or any other."""
        from cofferdam.workstation.tasks.gitrange import (
            GIT_DIFF_NAME_STATUS,
            GIT_HEAD,
            GIT_IS_ANCESTOR,
            GIT_IS_REPO,
            RANGE_ALLOWED_COMMANDS,
        )

        for argv in (GIT_HEAD, GIT_IS_REPO, GIT_IS_ANCESTOR, GIT_DIFF_NAME_STATUS):
            self.assertIn(argv, RANGE_ALLOWED_COMMANDS)
            for token in argv:
                self.assertIsInstance(token, str)
        self.assertIn("--find-renames", GIT_DIFF_NAME_STATUS)


# -- schema -------------------------------------------------------------------


class SchemaTests(unittest.TestCase):
    def test_a_fresh_store_is_schema_eleven_with_no_new_table(self):
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.tasks.store import TaskStore

        home = Path(tempfile.mkdtemp())
        config = load_config(home)
        config.ensure_dirs()
        store = TaskStore(config)
        try:
            store.storage_health()
            path = store.path
        finally:
            store.close()

        connection = sqlite3.connect(str(path))
        try:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            tables = sorted(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    " AND name NOT LIKE 'sqlite_%'"
                )
            )
        finally:
            connection.close()

        self.assertEqual("11", str(version))
        self.assertEqual(
            [
                "idempotency",
                "schema_meta",
                "task_artifacts",
                "task_change_claims",
                "task_claim_ingestion",
                "task_clarifications",
                "task_events",
                "task_turn_bounds",
                "task_turn_criteria",
                "task_turn_criteria_continuity",
                "task_turn_criterion_items",
                "task_turn_criterion_results",
                "task_turn_criterion_supersessions",
                "task_turn_evaluations",
                "task_turn_final_state",
                "task_turn_final_state_paths",
                "task_turn_git_baselines",
                "task_turns",
                "tasks",
            ],
            tables,
        )


# -- no surface grew ------------------------------------------------------------


class SurfaceTests(unittest.TestCase):
    def test_no_route_mentions_the_committed_range(self):
        service = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("committed_range_observed", service)
        self.assertNotIn("_record_committed_range", service)

    def test_the_bridge_gained_nothing(self):
        """No bridge Action, no bridge authority, no bridge reader."""
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        for path in bridge.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("committed_range", text, str(path))
            self.assertNotIn("_capture_terminal_boundary", text, str(path))

    def test_the_pwa_gained_no_control(self):
        """PR24's authoring surface is unchanged; PR24B stays deferred."""
        tasks_js = (REPO_ROOT / "web" / "tasks.js").read_text(encoding="utf-8")
        for banned in (
            "committed_range_observed",
            "terminal_boundary",
            "named check",
            "namedCheck",
        ):
            self.assertNotIn(banned, tasks_js)

    def test_no_global_task_verdict_appeared(self):
        """Acceptance stays target-turn only."""
        from cofferdam.workstation.tasks import acceptance

        source = Path(acceptance.__file__).read_text(encoding="utf-8")
        self.assertNotIn("def task_acceptance", source)
        self.assertNotIn("def task_verdict", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
