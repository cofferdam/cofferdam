"""M2K PR18 — the whole walk, in one isolated home with a real Git repository.

A real worker creates, deletes and restores files without committing any of it,
and at each boundary the derived current assessment answers every active
criterion from frozen rows alone. The uncommitted case is the whole point: a
HEAD-only probe would call a deleted-but-uncommitted file present, and a state
criterion that believed it would report a satisfied requirement over a file that
is gone.

The walk demonstrates the four things PR18 is for:

* a state criterion is decided by the **target turn's** observation, whether it
  was authored there or five turns earlier;
* an inherited state criterion breaks and repairs as the project does, with no
  carry-forward of any previous target's answer;
* a change criterion's answer does not move when final state contradicts it, and
  a state criterion's answer does not move when PR7's stored row does;
* each domain's evidence is required only by the criteria that consume it.

Also asserts the negative space structurally: no schema change, no evaluator or
observer movement, no aggregate, no `AGGREGATOR_VERSION`, no route, no bridge
operation and no PWA control.
"""

from __future__ import annotations

import ast
import json
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
from cofferdam.workstation.tasks.binding import (
    ASSESSMENT_RESOLVED,
    ASSESSMENT_UNAVAILABLE,
    CURRENT_ASSESSMENT_VERSION,
    DOMAIN_FINAL_STATE,
    DOMAIN_NOT_APPLICABLE,
    DOMAIN_TURN_CHANGE,
    REASON_EVALUATION_NOT_RECORDED,
    REASON_FINAL_STATE_LINEAGE_MISMATCH,
    REASON_FINAL_STATE_NOT_RECORDED,
    REASON_FINAL_STATE_OBSERVED,
    REASON_INHERITED_CHANGE_NOT_CURRENT,
    REASON_MANUAL_AUTHORITY,
    REASON_TURN_CHANGE_EVALUATED,
)
from cofferdam.workstation.tasks.continuity import CONTINUITY_MODEL_VERSION
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_MODEL_VERSION,
    PREDICATE_PATH_ABSENT,
    PREDICATE_PATH_EXISTS,
)
from cofferdam.workstation.tasks.evaluation import (
    EVALUATOR_VERSION,
    RESULT_MET,
    RESULT_NOT_MET,
    RESULT_UNVERIFIED,
)
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.finalstate import (
    FINAL_STATE_OBSERVER_VERSION,
    OBSERVATION_COMPLETE,
    PATH_ABSENT,
    PATH_PRESENT,
)
from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class ScriptedWorker(TaskAdapter):
    """Does whatever the next scripted step says, then reports it is ready."""

    adapter_id = "validation"
    display_name = "Scripted"

    def __init__(self):
        self.steps = []

    def capabilities(self):
        return AdapterCapabilities(start=True, followup=True, final_result=True)

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def _run(self, context):
        root = Path(context.project_root)
        if self.steps:
            self.steps.pop(0)(root)
        return AdapterOutcome(requested_state="ready_for_followup", final_result="done")

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


class Harness(unittest.TestCase):
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
        self.worker = ScriptedWorker()

        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        self.service = TaskService(
            self.config,
            self.store,
            registry,
            projects=load_projects(self.config, registry.ids()),
        )

    def git(self, *arguments):
        subprocess.run(
            ("git",) + arguments, cwd=self.root, check=True, capture_output=True
        )

    @contextmanager
    def sql(self):
        connection = sqlite3.connect(str(self.database))
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # -- authoring shorthands ------------------------------------------------

    def exists(self, path):
        return {"kind": "evidence", "predicate": PREDICATE_PATH_EXISTS, "path": path}

    def absent(self, path):
        return {"kind": "evidence", "predicate": PREDICATE_PATH_ABSENT, "path": path}

    def changed(self, path):
        return {"kind": "evidence", "predicate": "path_changed", "path": path}

    def manual(self):
        return {"kind": "manual", "description": "somebody must look at it"}

    def snapshot_id(self, task_id, turn):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def assess(self, task_id, turn):
        return self.service.current_criterion_assessment(task_id, turn)

    def shape(self, task_id, turn):
        """``(predicate-or-kind, source turn, result, reason)`` per criterion."""
        return [
            (item.predicate or item.kind, item.source_turn_number, item.result, item.reason)
            for item in self.assess(task_id, turn).assessments
        ]

    def start(self, criteria, step=None, prompt="scenario"):
        self.worker.steps = [step] if step else []
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt=prompt,
            origin="pwa",
            criteria=criteria,
            continuity={"mode": "root"},
        )
        return row.task_id

    def followup(self, task_id, criteria, continuity, step=None):
        self.worker.steps = [step] if step else []
        self.service.send_followup(
            task_id, "more", criteria=criteria, continuity=continuity
        )


class TheWholeWalk(Harness):
    def test_the_whole_walk(self):
        # 1. Schema v11, unchanged by PR18. This is derived read semantics.
        self.assertEqual(11, SCHEMA_VERSION)

        # 2-3. Turn 1 root: a state criterion, a change criterion, a manual one.
        # The worker creates a.txt and changes x.txt, committing neither.
        def turn_one(root):
            (root / "a.txt").write_text("made\n", encoding="utf-8")
            (root / "x.txt").write_text("changed\n", encoding="utf-8")

        task_id = self.start(
            [self.exists("a.txt"), self.changed("x.txt"), self.manual()], turn_one
        )

        # 4-5. PR14 stored the boundary and PR7 stored the turn-change judgement.
        first_observation = self.store.turn_final_state(task_id, 1)
        self.assertEqual(OBSERVATION_COMPLETE, first_observation.state)
        self.assertEqual(FINAL_STATE_OBSERVER_VERSION, first_observation.observer_version)
        stored = self.store.evaluation(task_id, 1)
        self.assertIsNotNone(stored)

        # 6. Three criteria, three domains, three kinds of authority.
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        state_one, change_one, manual_one = answer.assessments

        # A: met, from final state — and NOT from PR7, which recorded this exact
        # criterion as unverified/unsupported_capability at this very turn.
        self.assertEqual(RESULT_MET, state_one.result)
        self.assertEqual(DOMAIN_FINAL_STATE, state_one.domain)
        self.assertEqual(REASON_FINAL_STATE_OBSERVED, state_one.reason)
        self.assertEqual(first_observation.fingerprint, state_one.evidence_fingerprint)
        self.assertEqual(PATH_PRESENT, state_one.path_state)

        pr7_said = {item.criterion_id: item for item in stored.results}[
            state_one.criterion_id
        ]
        self.assertEqual(RESULT_UNVERIFIED, pr7_said.result)
        self.assertEqual("unsupported_capability", pr7_said.reason)
        self.assertNotEqual(pr7_said.result, state_one.result)

        # X: the exact PR7 result, bound by evaluation fingerprint.
        self.assertEqual(DOMAIN_TURN_CHANGE, change_one.domain)
        self.assertEqual(REASON_TURN_CHANGE_EVALUATED, change_one.reason)
        self.assertEqual(stored.evaluation_fingerprint, change_one.evidence_fingerprint)

        # M: unverified, no machine authority, no evidence identity invented.
        self.assertEqual(RESULT_UNVERIFIED, manual_one.result)
        self.assertEqual(REASON_MANUAL_AUTHORITY, manual_one.reason)
        self.assertIsNone(manual_one.evidence_fingerprint)

        # 7-9. Turn 2 extends with `path_absent(b.txt)` and `path_changed(y.txt)`.
        # The worker deletes a.txt, leaves b absent, and changes y.
        def turn_two(root):
            (root / "a.txt").unlink()
            (root / "y.txt").write_text("y\n", encoding="utf-8")

        self.followup(
            task_id,
            [self.absent("b.txt"), self.changed("y.txt")],
            {"mode": "extend", "predecessor_snapshot_id": self.snapshot_id(task_id, 1)},
            turn_two,
        )

        self.assertEqual(
            [
                # Inherited A: re-assessed at turn 2's boundary. a.txt is gone.
                (PREDICATE_PATH_EXISTS, 1, RESULT_NOT_MET, REASON_FINAL_STATE_OBSERVED),
                # Inherited X: a question about turn 1, unanswerable here.
                ("path_changed", 1, RESULT_UNVERIFIED, REASON_INHERITED_CHANGE_NOT_CURRENT),
                ("manual", 1, RESULT_UNVERIFIED, REASON_MANUAL_AUTHORITY),
                # B: met from turn 2's boundary — b.txt was never created.
                (PREDICATE_PATH_ABSENT, 2, RESULT_MET, REASON_FINAL_STATE_OBSERVED),
                # Y: the exact PR7 result for turn 2.
                ("path_changed", 2, self.assess(task_id, 2).assessments[4].result,
                 REASON_TURN_CHANGE_EVALUATED),
            ],
            self.shape(task_id, 2),
        )

        # 10. Turn 1's own answer did not move. A is still met AT TURN 1.
        self.assertEqual(RESULT_MET, self.assess(task_id, 1).assessments[0].result)

        # 11. Turn 3 restores a.txt. The inherited state criterion repairs.
        self.followup(
            task_id,
            [self.changed("z.txt")],
            {"mode": "extend", "predecessor_snapshot_id": self.snapshot_id(task_id, 2)},
            lambda root: (root / "a.txt").write_text("back\n", encoding="utf-8"),
        )
        third = self.assess(task_id, 3).assessments[0]
        self.assertEqual(RESULT_MET, third.result)
        self.assertEqual(DOMAIN_FINAL_STATE, third.domain)
        self.assertEqual(1, third.source_turn_number)
        self.assertEqual(3, third.target_turn_number)
        self.assertTrue(third.inherited)

        # The three target answers about ONE criterion are three distinct facts.
        history = [
            self.assess(task_id, turn).assessments[0] for turn in (1, 2, 3)
        ]
        self.assertEqual(
            [RESULT_MET, RESULT_NOT_MET, RESULT_MET], [item.result for item in history]
        )
        self.assertEqual(3, len({item.fingerprint for item in history}))
        self.assertEqual(3, len({item.evidence_fingerprint for item in history}))

        # 12-14. Turn 4 revises A away and adds C, a new state criterion.
        retired = self.store.turn_criteria(task_id, 1).criteria[0].criterion_id
        self.followup(
            task_id,
            [self.exists("c.txt")],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snapshot_id(task_id, 3),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired}
                ],
            },
            lambda root: (root / "c.txt").mkdir(),
        )
        fourth = self.assess(task_id, 4)
        self.assertNotIn(retired, [item.criterion_id for item in fourth.assessments])
        by_predicate = [item.predicate or item.kind for item in fourth.assessments]
        self.assertNotIn(PREDICATE_PATH_EXISTS, by_predicate[:-1])
        # The new state criterion is decided at turn 4's boundary. A directory is
        # a present object.
        newest = fourth.assessments[-1]
        self.assertEqual(PREDICATE_PATH_EXISTS, newest.predicate)
        self.assertEqual(RESULT_MET, newest.result)
        self.assertEqual("directory", newest.path_kind)
        # And a surviving inherited state criterion still uses turn 4's boundary.
        survivor = [
            item for item in fourth.assessments if item.predicate == PREDICATE_PATH_ABSENT
        ][0]
        self.assertEqual(RESULT_MET, survivor.result)
        self.assertEqual(DOMAIN_FINAL_STATE, survivor.domain)
        self.assertEqual(2, survivor.source_turn_number)
        self.assertEqual(4, survivor.target_turn_number)

        # 15-16. Turn 5 replaces: the prior lineage is cut and only the
        # replacement criteria are assessed.
        self.followup(
            task_id,
            [self.exists("d.txt"), self.absent("a.txt")],
            {
                "mode": "replace",
                "predecessor_snapshot_id": self.snapshot_id(task_id, 4),
            },
            lambda root: (root / "d.txt").write_text("d\n", encoding="utf-8"),
        )
        fifth = self.assess(task_id, 5)
        self.assertEqual(2, fifth.criterion_count)
        self.assertEqual(
            [
                (PREDICATE_PATH_EXISTS, 5, RESULT_MET, REASON_FINAL_STATE_OBSERVED),
                # a.txt was restored at turn 3 and never removed, so `absent` fails.
                (PREDICATE_PATH_ABSENT, 5, RESULT_NOT_MET, REASON_FINAL_STATE_OBSERVED),
            ],
            self.shape(task_id, 5),
        )

        # 21. Deleting the repository changes nothing: every answer is derived
        # from frozen rows, and nothing here looks at a disk.
        before = [self.assess(task_id, turn).fingerprint for turn in range(1, 6)]
        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        after = [self.assess(task_id, turn).fingerprint for turn in range(1, 6)]
        self.assertEqual(before, after)

        # 22. And reading cost the database nothing.
        with self.sql() as connection:
            digest = connection.execute(
                "SELECT count(*) AS n FROM task_turn_final_state"
            ).fetchone()["n"]
        for turn in range(1, 6):
            self.assess(task_id, turn)
        with self.sql() as connection:
            self.assertEqual(
                digest,
                connection.execute(
                    "SELECT count(*) AS n FROM task_turn_final_state"
                ).fetchone()["n"],
            )


class DomainConditionalInputsEndToEnd(Harness):
    """Each domain's evidence required only by the criteria that consume it."""

    def test_a_state_only_target_resolves_without_a_pr7_record(self):
        task_id = self.start(
            [self.exists("a.txt")],
            lambda root: (root / "a.txt").write_text("x\n", encoding="utf-8"),
        )
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_evaluations WHERE task_id = ?", (task_id,)
            )
        self.assertIsNone(self.store.evaluation(task_id, 1))
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(RESULT_MET, answer.assessments[0].result)

    def test_manual_and_state_and_inherited_change_resolve_without_one(self):
        task_id = self.start(
            [self.changed("x.txt"), self.manual()],
            lambda root: (root / "x.txt").write_text("x\n", encoding="utf-8"),
        )
        self.followup(
            task_id,
            [self.exists("x.txt")],
            {"mode": "extend", "predecessor_snapshot_id": self.snapshot_id(task_id, 1)},
        )
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_evaluations WHERE task_id = ? AND turn_number = 2",
                (task_id,),
            )
        answer = self.assess(task_id, 2)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(REASON_INHERITED_CHANGE_NOT_CURRENT, answer.assessments[0].reason)
        self.assertEqual(REASON_MANUAL_AUTHORITY, answer.assessments[1].reason)
        self.assertEqual(RESULT_MET, answer.assessments[2].result)

    def test_one_same_turn_change_criterion_makes_it_required_again(self):
        task_id = self.start(
            [self.exists("a.txt"), self.changed("a.txt")],
            lambda root: (root / "a.txt").write_text("x\n", encoding="utf-8"),
        )
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_evaluations WHERE task_id = ?", (task_id,)
            )
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_EVALUATION_NOT_RECORDED, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)

    def test_a_change_only_target_is_unaffected_by_a_deleted_observation(self):
        task_id = self.start(
            [self.changed("x.txt"), self.manual()],
            lambda root: (root / "x.txt").write_text("x\n", encoding="utf-8"),
        )
        before = self.assess(task_id, 1).fingerprint
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_final_state WHERE task_id = ?", (task_id,)
            )
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(before, answer.fingerprint)

    def test_a_state_criterion_without_an_observation_is_unverified_only(self):
        task_id = self.start(
            [self.changed("x.txt"), self.exists("a.txt")],
            lambda root: (
                (root / "x.txt").write_text("x\n", encoding="utf-8"),
                (root / "a.txt").write_text("a\n", encoding="utf-8"),
            ),
        )
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_final_state WHERE task_id = ?", (task_id,)
            )
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        # The change criterion is untouched by the other domain's gap.
        self.assertEqual(DOMAIN_TURN_CHANGE, answer.assessments[0].domain)
        self.assertEqual(REASON_TURN_CHANGE_EVALUATED, answer.assessments[0].reason)
        # And the state criterion says exactly what is missing, and does not look.
        self.assertEqual(RESULT_UNVERIFIED, answer.assessments[1].result)
        self.assertEqual(REASON_FINAL_STATE_NOT_RECORDED, answer.assessments[1].reason)
        self.assertTrue((self.root / "a.txt").exists())

    def test_the_observation_is_not_even_read_when_nothing_consumes_it(self):
        """Proven by corrupting it past use and getting an identical answer."""
        task_id = self.start(
            [self.changed("x.txt")],
            lambda root: (root / "x.txt").write_text("x\n", encoding="utf-8"),
        )
        before = self.assess(task_id, 1).fingerprint
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_final_state SET observation_fingerprint = ?,"
                " lineage_fingerprint = 'nonsense', observer_version = 9"
                " WHERE task_id = ?",
                ("0" * 64, task_id),
            )
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_RESOLVED, answer.state)
        self.assertEqual(before, answer.fingerprint)


class MalformedObservationEndToEnd(Harness):
    """Raw-SQL corruption of rows the service would never write. Fails closed."""

    def corrupt(self, task_id, **columns):
        assignments = ", ".join("%s = ?" % name for name in columns)
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_final_state SET %s WHERE task_id = ?" % assignments,
                tuple(columns.values()) + (task_id,),
            )

    def scenario(self):
        return self.start(
            [self.exists("a.txt")],
            lambda root: (root / "a.txt").write_text("x\n", encoding="utf-8"),
        )

    def test_a_lineage_fingerprint_mismatch_fails_the_set_closed(self):
        task_id = self.scenario()
        self.assertEqual(RESULT_MET, self.assess(task_id, 1).assessments[0].result)
        self.corrupt(task_id, lineage_fingerprint="f" * 64)
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual(REASON_FINAL_STATE_LINEAGE_MISMATCH, answer.unavailable_reason)
        self.assertEqual((), answer.assessments)

    def test_an_unsupported_observer_version_fails_the_set_closed(self):
        task_id = self.scenario()
        self.corrupt(task_id, observer_version=2)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, self.assess(task_id, 1).state)

    def test_a_tampered_path_state_is_caught_by_the_fingerprint(self):
        """The check that makes a raw edit to a path row detectable at all."""
        task_id = self.scenario()
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_final_state_paths SET path_state = 'absent',"
                " kind = NULL WHERE task_id = ?",
                (task_id,),
            )
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        # NOT `not_met`. The row was edited, not observed.
        self.assertEqual((), answer.assessments)

    def test_a_deleted_expected_path_row_is_never_read_as_absent(self):
        task_id = self.scenario()
        with self.sql() as connection:
            connection.execute(
                "DELETE FROM task_turn_final_state_paths WHERE task_id = ?", (task_id,)
            )
        answer = self.assess(task_id, 1)
        self.assertEqual(ASSESSMENT_UNAVAILABLE, answer.state)
        self.assertEqual((), answer.assessments)

    def test_nothing_is_repaired_by_reading(self):
        task_id = self.scenario()
        self.corrupt(task_id, lineage_fingerprint="f" * 64)
        for _ in range(3):
            self.assess(task_id, 1)
        with self.sql() as connection:
            row = connection.execute(
                "SELECT lineage_fingerprint FROM task_turn_final_state"
                " WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        self.assertEqual("f" * 64, row["lineage_fingerprint"])


class ConsistentReadTests(Harness):
    """One coherent snapshot, proven where the realistic race actually is.

    Final-state rows are immutable once captured, so the mutable edge is the PR7
    evaluation: it is written later by a bounded recovery pass, so it can appear
    between two autocommit reads. An answer stitched from an active set read
    before it and an evaluation read after it would describe a database state
    that never existed.
    """

    def test_the_inputs_come_from_one_snapshot(self):
        task_id = self.start(
            [self.exists("a.txt"), self.changed("x.txt")],
            lambda root: (
                (root / "a.txt").write_text("a\n", encoding="utf-8"),
                (root / "x.txt").write_text("x\n", encoding="utf-8"),
            ),
        )
        inputs = self.store.current_assessment_inputs(task_id, 1)
        self.assertIsNotNone(inputs.resolved)
        self.assertIsNotNone(inputs.evaluation)
        self.assertIsNotNone(inputs.final_state)
        self.assertTrue(inputs.turn_closed)
        # The resolve that decided *whether to fetch* is the same resolve the
        # binder runs on, so the decision and the reading cannot disagree.
        self.assertEqual(
            inputs.resolved.fingerprint, inputs.final_state.lineage_fingerprint
        )

    def test_a_state_only_target_carries_no_final_state_dependency_it_did_not_need(self):
        task_id = self.start(
            [self.changed("x.txt")],
            lambda root: (root / "x.txt").write_text("x\n", encoding="utf-8"),
        )
        inputs = self.store.current_assessment_inputs(task_id, 1)
        # A row exists in the database; it is deliberately not in the input set.
        self.assertEqual(
            OBSERVATION_COMPLETE, self.store.turn_final_state(task_id, 1).state
        )
        self.assertIsNone(inputs.final_state)

    def test_the_snapshot_read_and_the_public_read_agree(self):
        task_id = self.start(
            [self.exists("a.txt")],
            lambda root: (root / "a.txt").write_text("a\n", encoding="utf-8"),
        )
        inputs = self.store.current_assessment_inputs(task_id, 1)
        direct = self.store.turn_final_state(task_id, 1)
        self.assertEqual(direct.fingerprint, inputs.final_state.fingerprint)
        self.assertEqual(direct.paths, inputs.final_state.paths)


class ZeroMutationTests(Harness):
    def test_repeated_reads_leave_the_database_byte_identical(self):
        task_id = self.start(
            [self.exists("a.txt"), self.changed("x.txt"), self.manual()],
            lambda root: (
                (root / "a.txt").write_text("a\n", encoding="utf-8"),
                (root / "x.txt").write_text("x\n", encoding="utf-8"),
            ),
        )
        self.assess(task_id, 1)
        self.store.close()
        before = self.database.read_bytes()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        service = TaskService(
            self.config,
            store,
            registry,
            projects=load_projects(self.config, registry.ids()),
        )
        for _ in range(5):
            service.current_criterion_assessment(task_id, 1)
        store.close()
        self.assertEqual(before, self.database.read_bytes())

    def test_the_answer_survives_a_process_boundary_unchanged(self):
        task_id = self.start(
            [self.exists("a.txt")],
            lambda root: (root / "a.txt").write_text("a\n", encoding="utf-8"),
        )
        before = self.assess(task_id, 1).fingerprint
        self.store.close()
        store = TaskStore(self.config)
        self.addCleanup(store.close)
        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        service = TaskService(
            self.config,
            store,
            registry,
            projects=load_projects(self.config, registry.ids()),
        )
        self.assertEqual(
            before, service.current_criterion_assessment(task_id, 1).fingerprint
        )


class NegativeSpaceTests(unittest.TestCase):
    """What PR18 did not do, asserted structurally rather than by prose."""

    def module(self, name):
        path = REPO_ROOT / "cofferdam" / "workstation" / "tasks" / ("%s.py" % name)
        return path.read_text(encoding="utf-8")

    def test_the_schema_did_not_move(self):
        self.assertEqual(11, SCHEMA_VERSION)

    def test_only_the_assessment_version_moved(self):
        self.assertEqual(3, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(1, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(1, CONTINUITY_MODEL_VERSION)
        self.assertEqual(1, CRITERIA_MODEL_VERSION)
        self.assertEqual(3, ASSEMBLER_VERSION)

    def test_no_migration_was_added(self):
        source = self.module("store")
        tree = ast.parse(source)
        migrations = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_migrate")
        }
        self.assertNotIn("_migrate_to_v12", migrations)
        self.assertNotIn("_migrate_to_12", migrations)

    def test_the_evaluator_still_refuses_state_predicates(self):
        """PR7's historical record is correct and PR18 did not rewrite it."""
        from cofferdam.workstation.tasks import evaluation

        self.assertEqual(
            {"path_changed", "path_operation", "rename"}, set(evaluation._PREDICATES)
        )

    def test_the_observer_gained_no_new_semantics(self):
        from cofferdam.workstation.tasks import finalstate

        self.assertEqual(
            ("present", "absent", "unavailable"), finalstate.PATH_STATES
        )
        self.assertEqual(
            ("file", "directory", "symlink", "other"), finalstate.PATH_KINDS
        )

    def test_no_aggregate_exists_anywhere(self):
        from cofferdam.workstation.tasks import binding

        for forbidden in ("AGGREGATOR_VERSION", "aggregate", "acceptance_state"):
            self.assertFalse(hasattr(binding, forbidden))
            self.assertNotIn(forbidden, binding.__all__)

    def test_no_route_reaches_the_current_assessment(self):
        """The HTTP layer, not the task package that legitimately defines it."""
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
            self.assertNotIn("current_criterion_assessment", text, str(path))
            self.assertNotIn("CurrentAssessment", text, str(path))
            self.assertNotIn("turn_final_state", text, str(path))

    def test_the_pr8_assessment_response_was_not_widened(self):
        from cofferdam.workstation.tasks import assessment as assessment_module

        text = Path(assessment_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "current_criterion_assessment",
            "CurrentAssessment",
            "binding",
            "final_state",
        ):
            self.assertNotIn(forbidden, text)

    def test_no_bridge_operation_reaches_it(self):
        base = REPO_ROOT / "cofferdam" / "actions_bridge"
        if not base.exists():  # pragma: no cover - layout guard
            self.skipTest("no bridge package in this checkout")
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module.rsplit(".", 1)[-1])
                elif isinstance(node, ast.Import):
                    modules.update(a.name.rsplit(".", 1)[-1] for a in node.names)
            self.assertNotIn("binding", modules, str(path))
            self.assertNotIn("finalstate", modules, str(path))

    def test_no_pwa_control_reaches_it(self):
        base = REPO_ROOT / "cofferdam" / "workstation"
        for pattern in ("*.js", "*.html", "*.css"):
            for path in base.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn("current_criterion_assessment", text, str(path))
                self.assertNotIn("currentAssessment", text, str(path))
                self.assertNotIn("path_exists", text, str(path))

    def test_the_binder_runs_no_command(self):
        """From the AST. The module may *name* a subprocess in prose."""
        tree = ast.parse(self.module("binding"))
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        for forbidden in ("run", "Popen", "system", "exec", "eval", "open", "connect"):
            self.assertNotIn(forbidden, called)

    def test_no_named_check_vocabulary_was_introduced(self):
        from cofferdam.workstation.tasks import binding

        self.assertNotIn("named_check", binding.EVIDENCE_DOMAINS)
        for name in binding.__all__:
            self.assertNotIn("named_check", name.lower())

    def test_no_kind_predicate_was_introduced(self):
        from cofferdam.workstation.tasks.criteria import EVIDENCE_PREDICATES

        self.assertEqual(
            (
                "path_changed",
                "path_operation",
                "rename",
                "path_exists",
                "path_absent",
            ),
            EVIDENCE_PREDICATES,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
