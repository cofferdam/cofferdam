"""M2K PR23 — who may declare what a piece of work is judged against.

The acceptance stack has been complete and inert since PR21, because nothing
could declare its input. This is the first real caller path, and the two things
it must not get wrong are both about authority.

**Omission is never inference.** PR10 made "nobody declared a relationship" a
durable fact on purpose. A caller that omits `continuity` still gets
`not_declared` — no manufactured `root` on a first turn, no manufactured `extend`
on a follow-up. `NoHiddenDefaults` pins that from both ends: the same request
with and without the field produces materially different stored lineage, and the
difference is caused by the caller.

**The bridge is not an authority.** `require_task_caller` accepts two
credentials, so putting `criteria` and `continuity` in a shared allowlist would
have handed a remote Custom GPT user the power to declare what its own work is
measured against. The field list is per caller instead, and `AuthorityBoundary`
proves the bridge's request shape is exactly what it was.

**An invalid declaration reads as invalid.** The tracked
`ContinuityInvalid → ContinuityUnrecorded` debt is paid here, because it stops
being harmless the moment a caller can actually declare something.
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
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_DECLARED,
    CONTINUITY_EXTEND,
    CONTINUITY_NOT_DECLARED,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    EVIDENCE_PREDICATES,
)
from cofferdam.workstation.tasks.errors import (
    CODE_CONTINUITY_INVALID,
    CODE_CONTINUITY_UNRECORDED,
    CODE_CRITERIA_INVALID,
    ContinuityInvalid,
    ContinuityUnrecorded,
    CriteriaInvalid,
)
from cofferdam.workstation.tasks.projects import load_projects
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]

EXISTS_A = {"kind": "evidence", "predicate": "path_exists", "path": "a.txt"}
ABSENT_B = {"kind": "evidence", "predicate": "path_absent", "path": "b.txt"}
CHANGED_X = {"kind": "evidence", "predicate": "path_changed", "path": "x.txt"}


class ScriptedWorker:
    """Returns to `ready_for_followup`, so a second turn can be authored.

    The validation adapter's scenarios run to a terminal state, which is right
    for what they test and wrong here: these tests are about *declaring* a
    follow-up's requirements, so the task has to still be able to take one.
    """

    adapter_id = "validation"
    display_name = "Scripted"

    def __init__(self):
        self.dispatched = 0

    def capabilities(self):
        from cofferdam.workstation.tasks.adapters.protocol import AdapterCapabilities

        return AdapterCapabilities(start=True, followup=True, final_result=True)

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def _run(self, context):
        from cofferdam.workstation.tasks.adapters.protocol import AdapterOutcome

        self.dispatched += 1
        return AdapterOutcome(requested_state="ready_for_followup", final_result="done")

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


class ServiceCase(unittest.TestCase):
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
            json.dumps({"projects": [{
                "project_id": PROJECT_ID, "display_name": "Demo",
                "root": str(self.root), "adapters": ["validation"], "enabled": True,
            }]}),
            encoding="utf-8",
        )
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.database = self.store.path

        from cofferdam.workstation.tasks import build_registry

        self.worker = ScriptedWorker()
        adapters = type(build_registry(enable_validation_adapter=True))((self.worker,))
        self.service = TaskService(
            config, self.store, adapters, projects=load_projects(config, adapters.ids())
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

    def create(self, criteria=None, continuity=None, prompt="do the work"):
        row, _ = self.service.create_task(
            project_id=PROJECT_ID, adapter_id="validation", prompt=prompt,
            origin="pwa", criteria=criteria, continuity=continuity,
        )
        return row

    def snap(self, task_id, turn):
        return self.store.turn_criteria(task_id, turn).snapshot_id

    def lineage(self, task_id, turn):
        return self.store.turn_continuity(task_id, turn)


class NoHiddenDefaults(ServiceCase):
    """The rule PR10 exists for, now that a caller can actually exercise it."""

    def test_an_omitted_declaration_on_a_first_turn_stays_not_declared(self):
        row = self.create(criteria=[EXISTS_A])
        self.assertEqual(CONTINUITY_NOT_DECLARED, self.lineage(row.task_id, 1).state)

    def test_it_is_never_manufactured_into_root(self):
        row = self.create(criteria=[EXISTS_A])
        self.assertIsNone(self.lineage(row.task_id, 1).mode)
        self.assertNotEqual(CONTINUITY_ROOT, self.lineage(row.task_id, 1).mode)

    def test_an_explicit_root_is_stored_as_declared(self):
        row = self.create(criteria=[EXISTS_A], continuity={"mode": "root"})
        declaration = self.lineage(row.task_id, 1)
        self.assertEqual(CONTINUITY_DECLARED, declaration.state)
        self.assertEqual(CONTINUITY_ROOT, declaration.mode)

    def test_the_difference_is_caused_by_the_caller_and_nothing_else(self):
        """Same request twice, one field apart, two materially different facts."""
        omitted = self.create(criteria=[EXISTS_A])
        declared = self.create(criteria=[EXISTS_A], continuity={"mode": "root"})
        self.assertNotEqual(
            self.lineage(omitted.task_id, 1).state,
            self.lineage(declared.task_id, 1).state,
        )

    def test_the_service_has_no_first_turn_default(self):
        """Asserted from the source: no branch turns absence into a mode."""
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "service.py"
        ).read_text(encoding="utf-8")
        for forbidden in ('continuity or {"mode": "root"}',
                          'continuity = {"mode": "root"}',
                          'continuity or {"mode": "extend"}',
                          'or CONTINUITY_ROOT', 'or CONTINUITY_EXTEND'):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_http_layer_has_no_default_either(self):
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        for forbidden in ('"mode": "root"', '"mode": "extend"',
                          'payload.get("continuity", {'):
            self.assertNotIn(forbidden, source, forbidden)


class ExplicitModes(ServiceCase):
    """Each mode reaches the store as itself, validated by the existing owner."""

    def first(self):
        return self.create(criteria=[EXISTS_A], continuity={"mode": "root"})

    def test_extend_is_stored_as_extend(self):
        row = self.first()
        self.service.send_followup(
            row.task_id, "more", criteria=[ABSENT_B],
            continuity={"mode": "extend",
                        "predecessor_snapshot_id": self.snap(row.task_id, 1)},
        )
        self.assertEqual(CONTINUITY_EXTEND, self.lineage(row.task_id, 2).mode)

    def test_replace_is_stored_as_replace(self):
        row = self.first()
        self.service.send_followup(
            row.task_id, "more", criteria=[ABSENT_B],
            continuity={"mode": "replace",
                        "predecessor_snapshot_id": self.snap(row.task_id, 1)},
        )
        self.assertEqual(CONTINUITY_REPLACE, self.lineage(row.task_id, 2).mode)

    def test_revise_is_stored_with_its_relation(self):
        row = self.first()
        retired = self.store.turn_criteria(row.task_id, 1).criteria[0].criterion_id
        self.service.send_followup(
            row.task_id, "more", criteria=[ABSENT_B],
            continuity={
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(row.task_id, 1),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": retired}
                ],
            },
        )
        declaration = self.lineage(row.task_id, 2)
        self.assertEqual(CONTINUITY_REVISE, declaration.mode)
        self.assertEqual(1, declaration.relation_count)

    def test_an_omitted_follow_up_declaration_stays_not_declared(self):
        row = self.first()
        self.service.send_followup(row.task_id, "more", criteria=[ABSENT_B])
        self.assertEqual(CONTINUITY_NOT_DECLARED, self.lineage(row.task_id, 2).state)

    def test_it_is_never_manufactured_into_extend(self):
        row = self.first()
        self.service.send_followup(row.task_id, "more", criteria=[ABSENT_B])
        self.assertIsNone(self.lineage(row.task_id, 2).mode)


class CriteriaAuthoring(ServiceCase):
    def test_every_current_predicate_can_be_authored(self):
        specs = [
            {"kind": "evidence", "predicate": "path_changed", "path": "a.py"},
            {"kind": "evidence", "predicate": "path_operation", "path": "b.py",
             "operation": "created"},
            {"kind": "evidence", "predicate": "rename", "path": "c.py",
             "to_path": "d.py"},
            {"kind": "evidence", "predicate": "path_exists", "path": "e.txt"},
            {"kind": "evidence", "predicate": "path_absent", "path": "f.txt"},
            {"kind": "manual", "description": "a person looks"},
        ]
        row = self.create(criteria=specs, continuity={"mode": "root"})
        stored = self.store.turn_criteria(row.task_id, 1)
        self.assertEqual(CRITERIA_PRESENT, stored.state)
        self.assertEqual(6, stored.criterion_count)

    def test_the_authored_predicate_is_the_stored_predicate(self):
        """No convenience layer converts an action into a state, or back."""
        row = self.create(
            criteria=[{"kind": "evidence", "predicate": "path_operation",
                       "path": "a.txt", "operation": "created"}],
            continuity={"mode": "root"},
        )
        stored = self.store.turn_criteria(row.task_id, 1).criteria[0]
        self.assertEqual("path_operation", stored.predicate)
        self.assertNotEqual("path_exists", stored.predicate)

    def test_an_explicit_empty_declaration_is_not_provided(self):
        row = self.create(criteria=[], continuity={"mode": "root"})
        self.assertEqual(CRITERIA_NOT_PROVIDED,
                         self.store.turn_criteria(row.task_id, 1).state)

    def test_an_unknown_predicate_is_refused(self):
        with self.assertRaises(CriteriaInvalid):
            self.create(criteria=[{"kind": "evidence", "predicate": "path_is_huge",
                                   "path": "a.py"}])

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(CriteriaInvalid):
            self.create(criteria=[{"kind": "evidence", "predicate": "path_changed",
                                   "path": "a.py", "check_id": "lint"}])

    def test_no_named_check_vocabulary_exists(self):
        self.assertEqual(
            ("path_changed", "path_operation", "rename", "path_exists", "path_absent"),
            EVIDENCE_PREDICATES,
        )
        for forbidden in ("named_check", "command", "shell", "check_id"):
            self.assertNotIn(forbidden, EVIDENCE_PREDICATES)


class InvalidDeclarationIsInvalid(ServiceCase):
    """M2K PR23 — the tracked translation debt, and the proof it is paid."""

    def test_an_unknown_mode_is_refused_as_invalid(self):
        with self.assertRaises(ContinuityInvalid):
            self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})

    def test_an_unknown_relation_field_is_refused(self):
        with self.assertRaises(ContinuityInvalid):
            self.create(criteria=[EXISTS_A], continuity={"mode": "root", "why": "x"})

    def test_a_root_with_a_predecessor_is_refused(self):
        with self.assertRaises(ContinuityInvalid):
            self.create(criteria=[EXISTS_A],
                        continuity={"mode": "root",
                                    "predecessor_snapshot_id": "snapshot_" + "0" * 17})

    def test_a_stale_supersession_target_is_invalid_not_unrecorded(self):
        """The relational half, decided by the store — and the load-bearing case.

        Before PR23 this arrived as `continuity_unrecorded`: *Cofferdam could not
        write it*. It is nothing of the kind — the declaration names a criterion
        that is not active, which is the caller's mistake, and reporting it as a
        persistence failure sends somebody to look at the wrong system.
        """
        row = self.create(criteria=[EXISTS_A], continuity={"mode": "root"})
        with self.assertRaises(ContinuityInvalid) as caught:
            self.service.send_followup(
                row.task_id, "more", criteria=[ABSENT_B],
                continuity={
                    "mode": "revise",
                    "predecessor_snapshot_id": self.snap(row.task_id, 1),
                    "supersedes": [{"criterion_ordinal": 1,
                                    "predecessor_criterion_id": "criterion_" + "0" * 16}],
                },
            )
        self.assertEqual(CODE_CONTINUITY_INVALID, caught.exception.code)

    def test_it_is_specifically_not_the_unrecorded_code(self):
        row = self.create(criteria=[EXISTS_A], continuity={"mode": "root"})
        try:
            self.service.send_followup(
                row.task_id, "more", criteria=[ABSENT_B],
                continuity={
                    "mode": "revise",
                    "predecessor_snapshot_id": self.snap(row.task_id, 1),
                    "supersedes": [{"criterion_ordinal": 1,
                                    "predecessor_criterion_id": "criterion_" + "0" * 16}],
                },
            )
        except ContinuityInvalid as error:
            self.assertNotEqual(CODE_CONTINUITY_UNRECORDED, error.code)
        else:  # pragma: no cover - the call must refuse
            self.fail("the stale supersession was accepted")

    def test_the_two_errors_remain_distinguishable_types(self):
        self.assertNotEqual(ContinuityInvalid().code, ContinuityUnrecorded().code)
        self.assertFalse(issubclass(ContinuityInvalid, ContinuityUnrecorded))

    def test_a_refusal_never_echoes_the_submitted_value(self):
        try:
            self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})
        except ContinuityInvalid as error:
            text = "%s %s" % (error.message, error.detail)
            self.assertNotIn("preserve", text)
            self.assertNotIn(str(self.home), text)

    def test_a_refusal_carries_a_closed_reason_not_prose(self):
        try:
            self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})
        except ContinuityInvalid as error:
            self.assertTrue(error.detail)
            self.assertNotIn(" ", str(error.detail))
            for forbidden in ("Traceback", "sqlite", "/home/", "Error("):
                self.assertNotIn(forbidden, str(error.detail))


class InvalidInputDoesNotDispatch(ServiceCase):
    """A refused declaration must not reach a worker."""

    def poison(self):
        original_start = self.worker.start
        original_followup = self.worker.send_followup

        def poisoned(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the adapter was dispatched")

        self.worker.start = poisoned
        self.worker.send_followup = poisoned
        self.addCleanup(setattr, self.worker, "start", original_start)
        self.addCleanup(setattr, self.worker, "send_followup", original_followup)

    def test_invalid_criteria_never_reach_the_adapter(self):
        self.poison()
        with self.assertRaises(CriteriaInvalid):
            self.create(criteria=[{"kind": "evidence", "predicate": "nope",
                                   "path": "a.py"}])

    def test_invalid_continuity_never_reaches_the_adapter(self):
        self.poison()
        with self.assertRaises(ContinuityInvalid):
            self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})

    def test_a_shape_refusal_leaves_no_task_at_all(self):
        """Validated before the row exists, so there is nothing to clean up."""
        before = len(self.store.list_tasks())
        with self.assertRaises(ContinuityInvalid):
            self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})
        self.assertEqual(before, len(self.store.list_tasks()))

    def test_an_invalid_follow_up_declaration_dispatches_nothing(self):
        row = self.create(criteria=[EXISTS_A], continuity={"mode": "root"})
        self.poison()
        with self.assertRaises(ContinuityInvalid):
            self.service.send_followup(
                row.task_id, "more", criteria=[ABSENT_B],
                continuity={
                    "mode": "revise",
                    "predecessor_snapshot_id": self.snap(row.task_id, 1),
                    "supersedes": [{"criterion_ordinal": 1,
                                    "predecessor_criterion_id": "criterion_" + "0" * 16}],
                },
            )

    def test_a_refused_follow_up_leaves_the_task_correctable(self):
        """The relational refusal happens after the criteria snapshot is reserved.

        That is PR6's existing pre-dispatch behaviour and not something PR23
        changes: the snapshot is `captured`, which is the one replaceable state,
        so a corrected retry replaces it rather than colliding with it. What
        matters is that no worker saw anything.
        """
        row = self.create(criteria=[EXISTS_A], continuity={"mode": "root"})
        retired = self.store.turn_criteria(row.task_id, 1).criteria[0].criterion_id
        with self.assertRaises(ContinuityInvalid):
            self.service.send_followup(
                row.task_id, "more", criteria=[ABSENT_B],
                continuity={
                    "mode": "revise",
                    "predecessor_snapshot_id": self.snap(row.task_id, 1),
                    "supersedes": [{"criterion_ordinal": 1,
                                    "predecessor_criterion_id": "criterion_" + "0" * 16}],
                },
            )
        # Corrected and retried: the same turn, now with a real active target.
        self.service.send_followup(
            row.task_id, "more", criteria=[ABSENT_B],
            continuity={
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(row.task_id, 1),
                "supersedes": [{"criterion_ordinal": 1,
                                "predecessor_criterion_id": retired}],
            },
        )
        self.assertEqual(CONTINUITY_REVISE, self.lineage(row.task_id, 2).mode)


class NegativeSpaceTests(unittest.TestCase):
    def test_versions_and_schema_are_unchanged(self):
        from cofferdam.workstation.tasks.acceptance import AGGREGATOR_VERSION
        from cofferdam.workstation.tasks.binding import CURRENT_ASSESSMENT_VERSION
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
        from cofferdam.workstation.tasks.finalstate import FINAL_STATE_OBSERVER_VERSION
        from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION

        self.assertEqual(11, SCHEMA_VERSION)
        self.assertEqual(1, EVALUATOR_VERSION)
        self.assertEqual(1, RESOLVER_VERSION)
        self.assertEqual(2, FINAL_STATE_OBSERVER_VERSION)
        self.assertEqual(4, CURRENT_ASSESSMENT_VERSION)
        self.assertEqual(1, AGGREGATOR_VERSION)

    def test_no_post_dispatch_edit_endpoint_was_added(self):
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("/criteria", "/continuity", "edit_criteria",
                          "patch_criteria", "update_continuity"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_migration_was_added(self):
        from cofferdam.workstation.tasks import store as store_module

        tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
        migrations = {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_migrate")
        }
        self.assertNotIn("_migrate_to_v12", migrations)

    def test_the_adapter_protocol_cannot_supply_requirements(self):
        """Authority is the host's. A worker must not be able to author one."""
        from cofferdam.workstation.tasks.adapters import protocol

        import dataclasses

        # From the shapes, not the prose: the module legitimately *mentions*
        # continuity when explaining what an adapter must not decide.
        for name in ("AdapterContext", "AdapterOutcome", "AdapterCapabilities"):
            shape = getattr(protocol, name, None)
            if shape is None or not dataclasses.is_dataclass(shape):
                continue
            fields = {field.name for field in dataclasses.fields(shape)}
            for forbidden in ("criteria", "continuity", "supersedes", "predicate",
                              "acceptance", "requirements"):
                self.assertNotIn(forbidden, fields, "%s.%s" % (name, forbidden))

    def test_the_adapter_outcome_carries_no_requirement_field(self):
        import dataclasses

        from cofferdam.workstation.tasks.adapters.protocol import AdapterOutcome

        names = {field.name for field in dataclasses.fields(AdapterOutcome)}
        for forbidden in ("criteria", "continuity", "acceptance", "requirements"):
            self.assertNotIn(forbidden, names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
