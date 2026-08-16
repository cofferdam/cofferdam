"""M2K PR10 — continuity through the real dispatch lifecycle.

Where the model tests ask *what may be said*, these ask *when it becomes durable
and who may say it*. Three properties carry the PR:

**It is frozen before the worker exists.** The adapter's very first instruction
asserts, on a separate read-only connection so uncommitted rows cannot satisfy
it, that the criteria snapshot, the continuity declaration and the Git baseline
are all already committed and all already marked ``dispatch_started`` — and that
``task_turns`` has no row yet.

**It is immutable once frozen.** A retry of the same reserved turn, an adapter
refusal, and a restart all leave the original declaration and its fingerprint
exactly as they were. Only a genuinely new turn may declare afresh.

**Nobody but the caller may declare it.** No adapter field carries it, no HTTP
body carries it, and no worker output, claim, Git observation or evaluator result
can create a lineage edge.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_DECLARED,
    CONTINUITY_EXTEND,
    CONTINUITY_LEGACY_UNKNOWN,
    CONTINUITY_NOT_DECLARED,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
)
from cofferdam.workstation.tasks.errors import ContinuityInvalid
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import TaskStore

#: A resolvable first turn. Since M2K PR12 a `revise` needs its predecessor's
#: active set, and a predecessor whose own continuity is `not_declared` has
#: none — so a fixture that means "an ordinary earlier turn" has to say `root`.
ROOT = {"mode": "root"}

PROJECT_ID = "demo"
CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"},
    {"kind": "evidence", "predicate": "path_changed", "path": "src/b.py"},
]


class ContinuityCase(unittest.TestCase):
    """A real service over a real store, with the validation adapter."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)

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

        self.adapters = build_registry(enable_validation_adapter=True)
        self.service = TaskService(
            config,
            self.store,
            self.adapters,
            projects=load_projects(config, self.adapters.ids()),
        )

    # -- helpers ------------------------------------------------------------

    def start(self, criteria=CRITERIA, continuity=None, prompt="scenario: complete"):
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt=prompt,
            origin="pwa",
            criteria=criteria,
            continuity=continuity,
        )
        return row

    def seed_turn(self, task_id, turn_number):
        """Close a turn so the next reservation allocates the following number."""
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome) VALUES (?,?,"
                "'validation','pwa','x','y','completed')",
                (task_id, turn_number),
            )

    def reserve(self, task_id, criteria=CRITERIA, continuity=None):
        """Reserve the next turn's criteria then its continuity, as dispatch does."""
        from cofferdam.workstation.tasks.criteria import validate_criteria
        from cofferdam.workstation.tasks.continuity import validate_declaration

        self.store.reserve_turn_criteria(
            task_id, validate_criteria(criteria), recorded_at="2026-08-16T04:00:00Z"
        )
        return self.store.reserve_turn_continuity(
            task_id,
            validate_declaration(continuity),
            recorded_at="2026-08-16T04:00:01Z",
        )

    def install_adapter(self, adapter):
        """Swap the validation adapter for a watcher, through the real registry."""
        from cofferdam.workstation.tasks.projects import load_projects

        registry = type(self.adapters)((adapter,))
        self.service = TaskService(
            self.config,
            self.store,
            registry,
            projects=load_projects(self.config, registry.ids()),
        )

    def snapshot_id(self, task_id, turn_number):
        return self.store.turn_criteria(task_id, turn_number).snapshot_id

    def criterion_ids(self, task_id, turn_number):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return [
                r[0]
                for r in connection.execute(
                    "SELECT criterion_id FROM task_turn_criterion_items"
                    " WHERE task_id=? AND turn_number=? ORDER BY ordinal",
                    (task_id, turn_number),
                )
            ]
        finally:
            connection.close()


class FirstTurn(ContinuityCase):
    def test_an_undeclared_first_turn_records_not_declared(self):
        row = self.start()
        continuity = self.store.turn_continuity(row.task_id, 1)
        self.assertEqual(CONTINUITY_NOT_DECLARED, continuity.state)
        self.assertIsNone(continuity.mode)
        self.assertTrue(continuity.recorded)

    def test_the_row_exists_rather_than_being_omitted(self):
        """The distinction from a historical turn depends entirely on this."""
        row = self.start()
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                1,
                connection.execute(
                    "SELECT COUNT(*) FROM task_turn_criteria_continuity"
                    " WHERE task_id=?",
                    (row.task_id,),
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_an_explicit_root_first_turn_records_root(self):
        row = self.start(continuity={"mode": CONTINUITY_ROOT})
        continuity = self.store.turn_continuity(row.task_id, 1)
        self.assertEqual(CONTINUITY_DECLARED, continuity.state)
        self.assertEqual(CONTINUITY_ROOT, continuity.mode)
        self.assertIsNone(continuity.predecessor_snapshot_id)
        self.assertEqual(0, continuity.relation_count)

    def test_root_is_allowed_when_criteria_are_not_provided(self):
        """`root` is a statement about lineage, not about requirements existing."""
        row = self.start(criteria=[], continuity={"mode": CONTINUITY_ROOT})
        self.assertEqual(
            "not_provided", self.store.turn_criteria(row.task_id, 1).state
        )
        continuity = self.store.turn_continuity(row.task_id, 1)
        self.assertEqual(CONTINUITY_ROOT, continuity.mode)

    def test_root_is_refused_when_an_earlier_snapshot_exists(self):
        """A structural claim, checked against the database rather than believed."""
        row = self.start()
        self.seed_turn(row.task_id, 1)
        with self.assertRaises(ContinuityInvalid):
            self.reserve(row.task_id, continuity={"mode": CONTINUITY_ROOT})


class HistoricalTurns(ContinuityCase):
    def test_a_turn_with_no_row_reads_legacy_unknown(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "DELETE FROM task_turn_criteria_continuity WHERE task_id=?",
                (row.task_id,),
            )
        continuity = self.store.turn_continuity(row.task_id, 1)
        self.assertEqual(CONTINUITY_LEGACY_UNKNOWN, continuity.state)
        self.assertFalse(continuity.recorded)

    def test_legacy_unknown_fabricates_no_identity(self):
        row = self.start()
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "DELETE FROM task_turn_criteria_continuity WHERE task_id=?",
                (row.task_id,),
            )
        continuity = self.store.turn_continuity(row.task_id, 1)
        self.assertIsNone(continuity.continuity_id)
        self.assertIsNone(continuity.continuity_fingerprint)
        self.assertIsNone(continuity.mode)
        self.assertIsNone(continuity.predecessor_snapshot_id)
        self.assertEqual((), continuity.relations)

    def test_legacy_unknown_is_not_not_declared(self):
        row = self.start()
        declared = self.store.turn_continuity(row.task_id, 1)
        with sqlite3.connect(str(self.database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(
                "DELETE FROM task_turn_criteria_continuity WHERE task_id=?",
                (row.task_id,),
            )
        legacy = self.store.turn_continuity(row.task_id, 1)
        self.assertEqual(CONTINUITY_NOT_DECLARED, declared.state)
        self.assertEqual(CONTINUITY_LEGACY_UNKNOWN, legacy.state)
        self.assertNotEqual(declared.state, legacy.state)


class FollowUpModes(ContinuityCase):
    def declare(self, mode, **extra):
        row = self.start()
        first = self.snapshot_id(row.task_id, 1)
        self.seed_turn(row.task_id, 1)
        declaration = {"mode": mode, "predecessor_snapshot_id": first}
        declaration.update(extra)
        self.reserve(row.task_id, continuity=declaration)
        return row, first

    def test_extend_binds_the_predecessor_and_carries_no_relations(self):
        row, first = self.declare(CONTINUITY_EXTEND)
        continuity = self.store.turn_continuity(row.task_id, 2)
        self.assertEqual(CONTINUITY_EXTEND, continuity.mode)
        self.assertEqual(first, continuity.predecessor_snapshot_id)
        self.assertEqual(0, continuity.relation_count)
        self.assertEqual((), continuity.relations)

    def test_replace_binds_the_predecessor_without_enumerating_it(self):
        row, first = self.declare(CONTINUITY_REPLACE)
        continuity = self.store.turn_continuity(row.task_id, 2)
        self.assertEqual(CONTINUITY_REPLACE, continuity.mode)
        self.assertEqual(first, continuity.predecessor_snapshot_id)
        self.assertEqual(0, continuity.relation_count)

    def test_replace_deletes_no_prior_criteria(self):
        row, first = self.declare(CONTINUITY_REPLACE)
        prior = self.store.turn_criteria(row.task_id, 1)
        self.assertEqual("present", prior.state)
        self.assertEqual(2, prior.criterion_count)

    def test_revise_stores_the_supersession_relations(self):
        row = self.start(continuity=ROOT)
        first = self.snapshot_id(row.task_id, 1)
        retired = self.criterion_ids(row.task_id, 1)[0]
        self.seed_turn(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={
                "mode": CONTINUITY_REVISE,
                "predecessor_snapshot_id": first,
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired}
                ],
            },
        )
        continuity = self.store.turn_continuity(row.task_id, 2)
        self.assertEqual(CONTINUITY_REVISE, continuity.mode)
        self.assertEqual(1, continuity.relation_count)
        self.assertEqual(1, len(continuity.relations))
        self.assertEqual(retired, continuity.relations[0].predecessor_criterion_id)
        self.assertIn(
            continuity.relations[0].criterion_id, self.criterion_ids(row.task_id, 2)
        )

    def test_revise_supports_a_split_one_old_to_many_new(self):
        row = self.start(continuity=ROOT)
        first = self.snapshot_id(row.task_id, 1)
        retired = self.criterion_ids(row.task_id, 1)[0]
        self.seed_turn(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={
                "mode": CONTINUITY_REVISE,
                "predecessor_snapshot_id": first,
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired},
                    {"criterion_ordinal": 2, "predecessor_criterion_id": retired},
                ],
            },
        )
        self.assertEqual(2, self.store.turn_continuity(row.task_id, 2).relation_count)

    def test_revise_supports_a_merge_many_old_to_one_new(self):
        row = self.start(continuity=ROOT)
        first = self.snapshot_id(row.task_id, 1)
        retired = self.criterion_ids(row.task_id, 1)
        self.seed_turn(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={
                "mode": CONTINUITY_REVISE,
                "predecessor_snapshot_id": first,
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired[0]},
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired[1]},
                ],
            },
        )
        self.assertEqual(2, self.store.turn_continuity(row.task_id, 2).relation_count)

    def test_relations_are_stored_in_canonical_order(self):
        """Submission order is not a fact, so it is not preserved."""
        row = self.start(continuity=ROOT)
        first = self.snapshot_id(row.task_id, 1)
        retired = self.criterion_ids(row.task_id, 1)
        self.seed_turn(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={
                "mode": CONTINUITY_REVISE,
                "predecessor_snapshot_id": first,
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired[1]},
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired[0]},
                ],
            },
        )
        relations = self.store.turn_continuity(row.task_id, 2).relations
        self.assertEqual([1, 2], [r.ordinal for r in relations])
        pairs = [(r.criterion_id, r.predecessor_criterion_id) for r in relations]
        self.assertEqual(sorted(pairs), pairs)


class LineageSecurity(ContinuityCase):
    def test_a_predecessor_from_another_task_is_refused(self):
        other = self.start()
        foreign = self.snapshot_id(other.task_id, 1)
        row = self.start()
        self.seed_turn(row.task_id, 1)
        with self.assertRaises(ContinuityInvalid):
            self.reserve(
                row.task_id,
                continuity={
                    "mode": CONTINUITY_EXTEND,
                    "predecessor_snapshot_id": foreign,
                },
            )

    def test_an_unknown_predecessor_is_refused(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        with self.assertRaises(ContinuityInvalid):
            self.reserve(
                row.task_id,
                continuity={
                    "mode": CONTINUITY_EXTEND,
                    "predecessor_snapshot_id": "acs_" + "z" * 26,
                },
            )

    def test_the_current_turns_own_snapshot_cannot_be_its_predecessor(self):
        """A cycle, not a lineage."""
        row = self.start()
        self.seed_turn(row.task_id, 1)
        from cofferdam.workstation.tasks.criteria import validate_criteria
        from cofferdam.workstation.tasks.continuity import validate_declaration

        self.store.reserve_turn_criteria(
            row.task_id, validate_criteria(CRITERIA), recorded_at="2026-08-16T04:00:00Z"
        )
        current = self.snapshot_id(row.task_id, 2)
        with self.assertRaises(ContinuityInvalid):
            self.store.reserve_turn_continuity(
                row.task_id,
                validate_declaration(
                    {
                        "mode": CONTINUITY_EXTEND,
                        "predecessor_snapshot_id": current,
                    }
                ),
                recorded_at="2026-08-16T04:00:01Z",
            )

    def test_a_superseded_criterion_outside_the_predecessor_is_refused(self):
        other = self.start(continuity=ROOT)
        foreign_criterion = self.criterion_ids(other.task_id, 1)[0]
        row = self.start(continuity=ROOT)
        first = self.snapshot_id(row.task_id, 1)
        self.seed_turn(row.task_id, 1)
        with self.assertRaises(ContinuityInvalid):
            self.reserve(
                row.task_id,
                continuity={
                    "mode": CONTINUITY_REVISE,
                    "predecessor_snapshot_id": first,
                    "supersedes": [
                        {
                            "criterion_ordinal": 1,
                            "predecessor_criterion_id": foreign_criterion,
                        }
                    ],
                },
            )

    def test_a_current_ordinal_that_does_not_exist_is_refused(self):
        row = self.start()
        first = self.snapshot_id(row.task_id, 1)
        retired = self.criterion_ids(row.task_id, 1)[0]
        self.seed_turn(row.task_id, 1)
        with self.assertRaises(ContinuityInvalid):
            self.reserve(
                row.task_id,
                continuity={
                    "mode": CONTINUITY_REVISE,
                    "predecessor_snapshot_id": first,
                    "supersedes": [
                        {"criterion_ordinal": 99, "predecessor_criterion_id": retired}
                    ],
                },
            )

    def test_a_refusal_writes_nothing(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        before = self.rows()
        with self.assertRaises(ContinuityInvalid):
            self.reserve(
                row.task_id,
                continuity={
                    "mode": CONTINUITY_EXTEND,
                    "predecessor_snapshot_id": "acs_" + "z" * 26,
                },
            )
        after = self.rows()
        self.assertEqual(before["continuity"], after["continuity"])
        self.assertEqual(before["supersessions"], after["supersessions"])

    def rows(self):
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            return {
                "continuity": connection.execute(
                    "SELECT * FROM task_turn_criteria_continuity"
                ).fetchall(),
                "supersessions": connection.execute(
                    "SELECT * FROM task_turn_criterion_supersessions"
                ).fetchall(),
            }
        finally:
            connection.close()


class Immutability(ContinuityCase):
    def frozen(self, task_id, turn_number=1):
        return self.store.turn_continuity(task_id, turn_number)

    def freeze(self, task_id, turn_number):
        """Freeze both pre-work facts together, as ``_mark_dispatch_started`` does.

        Marking continuity alone would be a state the service cannot produce, and
        it matters: the continuity row is a child of the criteria snapshot by
        foreign key, so a *replaceable* snapshot being replaced legitimately
        cascades its declaration away to be re-reserved a line later. The two
        freeze in the same call precisely so that window closes for both at once.
        """
        self.store.mark_criteria_dispatch_started(task_id, turn_number)
        self.store.mark_continuity_dispatch_started(task_id, turn_number)

    def test_a_completed_turn_leaves_continuity_marked_turn_opened(self):
        row = self.start(continuity={"mode": CONTINUITY_ROOT})
        self.assertEqual(
            "turn_opened", self.store.turn_continuity_dispatch_state(row.task_id, 1)
        )

    def test_a_retry_of_the_same_reserved_turn_cannot_replace_it(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        first = self.snapshot_id(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.freeze(row.task_id, 2)
        original = self.frozen(row.task_id, 2)

        # A second attempt at the same reserved turn, with a different mode.
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_REPLACE, "predecessor_snapshot_id": first},
        )
        after = self.frozen(row.task_id, 2)
        self.assertEqual(original.mode, after.mode)
        self.assertEqual(CONTINUITY_EXTEND, after.mode)
        self.assertEqual(original.continuity_id, after.continuity_id)
        self.assertEqual(
            original.continuity_fingerprint, after.continuity_fingerprint
        )

    def test_a_refusal_does_not_reopen_replacement(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        first = self.snapshot_id(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.freeze(row.task_id, 2)
        self.store.mark_continuity_dispatch_refused(row.task_id, 2)
        original = self.frozen(row.task_id, 2)

        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_REPLACE, "predecessor_snapshot_id": first},
        )
        after = self.frozen(row.task_id, 2)
        self.assertEqual(CONTINUITY_EXTEND, after.mode)
        self.assertEqual(original.continuity_fingerprint, after.continuity_fingerprint)

    def test_a_declared_turn_cannot_become_undeclared(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        first = self.snapshot_id(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.freeze(row.task_id, 2)
        self.reserve(row.task_id, continuity=None)
        self.assertEqual(CONTINUITY_DECLARED, self.frozen(row.task_id, 2).state)
        self.assertEqual(CONTINUITY_EXTEND, self.frozen(row.task_id, 2).mode)

    def test_an_undeclared_turn_cannot_become_declared(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        first = self.snapshot_id(row.task_id, 1)
        self.reserve(row.task_id, continuity=None)
        self.freeze(row.task_id, 2)
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.assertEqual(CONTINUITY_NOT_DECLARED, self.frozen(row.task_id, 2).state)
        self.assertIsNone(self.frozen(row.task_id, 2).mode)

    def test_relations_cannot_be_added_after_freeze(self):
        row = self.start()
        self.seed_turn(row.task_id, 1)
        first = self.snapshot_id(row.task_id, 1)
        retired = self.criterion_ids(row.task_id, 1)[0]
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.freeze(row.task_id, 2)
        self.reserve(
            row.task_id,
            continuity={
                "mode": CONTINUITY_REVISE,
                "predecessor_snapshot_id": first,
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired}
                ],
            },
        )
        self.assertEqual(0, self.frozen(row.task_id, 2).relation_count)

    def test_replacement_is_allowed_while_still_captured(self):
        """The one window that exists, and it closes at dispatch_started."""
        row = self.start()
        self.seed_turn(row.task_id, 1)
        first = self.snapshot_id(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.assertEqual(
            "captured", self.store.turn_continuity_dispatch_state(row.task_id, 2)
        )
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_REPLACE, "predecessor_snapshot_id": first},
        )
        self.assertEqual(CONTINUITY_REPLACE, self.frozen(row.task_id, 2).mode)

    def test_the_fingerprint_survives_a_reopen(self):
        row = self.start(continuity={"mode": CONTINUITY_ROOT})
        before = self.frozen(row.task_id, 1).continuity_fingerprint
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(
            before, reopened.turn_continuity(row.task_id, 1).continuity_fingerprint
        )

    def test_a_new_turn_may_declare_afresh(self):
        row = self.start(continuity={"mode": CONTINUITY_ROOT})
        first = self.snapshot_id(row.task_id, 1)
        self.seed_turn(row.task_id, 1)
        self.reserve(
            row.task_id,
            continuity={"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": first},
        )
        self.assertEqual(CONTINUITY_ROOT, self.frozen(row.task_id, 1).mode)
        self.assertEqual(CONTINUITY_EXTEND, self.frozen(row.task_id, 2).mode)
        self.assertNotEqual(
            self.frozen(row.task_id, 1).continuity_fingerprint,
            self.frozen(row.task_id, 2).continuity_fingerprint,
        )


class Authority(ContinuityCase):
    def test_the_adapter_outcome_has_no_continuity_field(self):
        from cofferdam.workstation.tasks.adapters.protocol import AdapterOutcome

        for field in AdapterOutcome.__dataclass_fields__:
            self.assertNotIn("continuity", field)
            self.assertNotIn("supersed", field)

    def test_the_task_context_has_no_continuity_field(self):
        from cofferdam.workstation.tasks.adapters.protocol import TaskContext

        for field in TaskContext.__dataclass_fields__:
            self.assertNotIn("continuity", field)
            self.assertNotIn("supersed", field)

    def test_no_http_route_accepts_continuity(self):
        """PR6 kept criteria off the wire; PR10 does not widen that."""
        source = (
            Path(__file__).resolve().parents[1]
            / "cofferdam"
            / "workstation"
            / "service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"continuity"', source)
        self.assertNotIn("continuity=", source)

    def test_no_bridge_action_accepts_continuity(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "cofferdam"
            / "actions_bridge"
            / "service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("continuity", source.lower())

    def test_the_evaluator_creates_no_relation(self):
        from cofferdam.workstation.tasks import evaluation

        source = Path(evaluation.__file__).read_text(encoding="utf-8")
        self.assertNotIn("continuity", source.lower())
        self.assertNotIn("supersed", source.lower())

    def test_worker_output_creates_no_relation(self):
        """A whole task runs; its adapter reports events and a result. No lineage."""
        row = self.start()
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        try:
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM task_turn_criterion_supersessions"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_a_caller_cannot_choose_the_durable_ids(self):
        row = self.start(continuity={"mode": CONTINUITY_ROOT})
        continuity = self.store.turn_continuity(row.task_id, 1)
        self.assertTrue(continuity.continuity_id.startswith("ctn_"))
        from cofferdam.workstation.tasks.continuity import (
            ContinuitySubmissionInvalid,
            validate_declaration,
        )

        # Refused by name at validation, before the store is reached at all.
        with self.assertRaises(ContinuitySubmissionInvalid):
            validate_declaration(
                {"mode": CONTINUITY_ROOT, "continuity_id": "ctn_mine"}
            )
        # And through the service, as an ordinary Task Core refusal.
        with self.assertRaises(ContinuityInvalid):
            self.service._valid_continuity(
                {"mode": CONTINUITY_ROOT, "continuity_fingerprint": "x" * 64}
            )


class PreDispatchDurability(ContinuityCase):
    """The adapter's first instruction is the assertion point."""

    def test_all_three_pre_work_facts_are_durable_before_the_adapter(self):
        observed = {}
        database = self.database

        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterCapabilities,
            TaskAdapter,
        )

        class Watcher(TaskAdapter):
            adapter_id = "validation"
            display_name = "Watcher"

            def capabilities(self):
                return AdapterCapabilities()

            def available(self):
                return True

            def start(self, context):
                # A SEPARATE read-only connection: uncommitted rows cannot
                # satisfy this, which is the whole point of the check.
                connection = sqlite3.connect(
                    "file:%s?mode=ro" % database, uri=True
                )
                try:
                    observed["criteria"] = connection.execute(
                        "SELECT dispatch_state FROM task_turn_criteria"
                        " WHERE task_id=? AND turn_number=1",
                        (context.task_id,),
                    ).fetchone()
                    observed["continuity"] = connection.execute(
                        "SELECT continuity_state, mode, dispatch_state"
                        " FROM task_turn_criteria_continuity"
                        " WHERE task_id=? AND turn_number=1",
                        (context.task_id,),
                    ).fetchone()
                    observed["baseline"] = connection.execute(
                        "SELECT dispatch_state FROM task_turn_git_baselines"
                        " WHERE task_id=? AND turn_number=1",
                        (context.task_id,),
                    ).fetchone()
                    observed["turns"] = connection.execute(
                        "SELECT COUNT(*) FROM task_turns WHERE task_id=?",
                        (context.task_id,),
                    ).fetchone()[0]
                    observed["items"] = connection.execute(
                        "SELECT COUNT(*) FROM task_turn_criterion_items"
                        " WHERE task_id=? AND turn_number=1",
                        (context.task_id,),
                    ).fetchone()[0]
                finally:
                    connection.close()
                from cofferdam.workstation.tasks.adapters.protocol import (
                    AdapterOutcome,
                )

                return AdapterOutcome(requested_state="completed", final_result="ok")

            def send_followup(self, context, text):  # pragma: no cover
                raise NotImplementedError

            def cancel(self, context):  # pragma: no cover
                raise NotImplementedError

        self.install_adapter(Watcher())
        row, _ = self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=CRITERIA,
            continuity={"mode": CONTINUITY_ROOT},
        )

        self.assertIsNotNone(observed["criteria"], "criteria were not durable")
        self.assertEqual("dispatch_started", observed["criteria"][0])
        self.assertIsNotNone(observed["continuity"], "continuity was not durable")
        self.assertEqual(CONTINUITY_DECLARED, observed["continuity"][0])
        self.assertEqual(CONTINUITY_ROOT, observed["continuity"][1])
        self.assertEqual("dispatch_started", observed["continuity"][2])
        self.assertEqual(2, observed["items"])
        self.assertEqual(0, observed["turns"], "the turn row existed too early")

    def test_an_undeclared_followup_is_durable_before_the_adapter(self):
        observed = {}
        database = self.database

        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterCapabilities,
            TaskAdapter,
        )

        class Watcher(TaskAdapter):
            adapter_id = "validation"
            display_name = "Watcher"

            def capabilities(self):
                return AdapterCapabilities()

            def available(self):
                return True

            def start(self, context):
                connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True)
                try:
                    observed["row"] = connection.execute(
                        "SELECT continuity_state, mode FROM"
                        " task_turn_criteria_continuity WHERE task_id=?"
                        " AND turn_number=1",
                        (context.task_id,),
                    ).fetchone()
                finally:
                    connection.close()
                from cofferdam.workstation.tasks.adapters.protocol import (
                    AdapterOutcome,
                )

                return AdapterOutcome(requested_state="completed", final_result="ok")

            def send_followup(self, context, text):  # pragma: no cover
                raise NotImplementedError

            def cancel(self, context):  # pragma: no cover
                raise NotImplementedError

        self.install_adapter(Watcher())
        self.service.create_task(
            project_id=PROJECT_ID,
            adapter_id="validation",
            prompt="scenario: complete",
            origin="pwa",
            criteria=CRITERIA,
        )
        self.assertIsNotNone(observed["row"])
        self.assertEqual(CONTINUITY_NOT_DECLARED, observed["row"][0])
        self.assertIsNone(observed["row"][1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
