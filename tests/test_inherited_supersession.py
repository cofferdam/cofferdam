"""M2K PR12 — a revise may retire whatever its predecessor actually stands on.

PR10 validated a supersession's old side against the criteria **stored in** the
declared predecessor's snapshot. PR11's resolver validated it against the
predecessor's **resolved active set**, and the two disagree the moment a
requirement is inherited: a criterion introduced at turn 1 and still live at
turn 2 through an `extend` is part of what turn 3 stands on, but it is not one of
turn 2's own rows.

PR11 recorded that as a limitation to revisit. This module is the revisit. The
write-time rule is now the read-time rule, and there is one implementation of it.

What does **not** change: a criterion that is historically real but no longer
active is still refused — retired by an earlier `revise`, cut away by a
`replace`, or belonging to another task. Widening which declarations are accepted
is not the same as accepting more of them blindly, and every test below that ends
in a refusal is the point of the ones that do not.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_MODEL_VERSION,
    REASON_PREDECESSOR_LINEAGE_UNAVAILABLE,
    REASON_RELATION_CURRENT_UNKNOWN,
    REASON_RELATION_PREDECESSOR_NOT_ACTIVE,
    REASON_RELATION_PREDECESSOR_UNKNOWN,
    continuity_fingerprint,
    validate_declaration,
)
from cofferdam.workstation.tasks.criteria import validate_criteria
from cofferdam.workstation.tasks.continuity import ContinuitySubmissionInvalid
from cofferdam.workstation.tasks.errors import ContinuityInvalid
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.lineage import RESOLVER_VERSION, resolve
from cofferdam.workstation.tasks.service import TaskService
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore

PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]


class SupersessionCase(unittest.TestCase):
    """A real store over an isolated home, driven the way dispatch drives it."""

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
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.store.storage_health()
        self.database = self.store.path

        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects

        self.adapters = build_registry(enable_validation_adapter=True)
        self.service = TaskService(
            self.config,
            self.store,
            self.adapters,
            projects=load_projects(self.config, self.adapters.ids()),
        )
        self.task_id = self.make_task()

    # -- fixtures ------------------------------------------------------------

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

    def make_task(self, task_id=None):
        task_id = task_id or new_task_id()
        with self.sql() as connection:
            connection.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, title, prompt)"
                " VALUES (?,'cor','pwa','validation',?,'running','x','x','t','p')",
                (task_id, PROJECT_ID),
            )
        return task_id

    def criteria_for(self, *labels):
        return [
            {"kind": "evidence", "predicate": "path_changed", "path": "%s.py" % label}
            for label in labels
        ]

    def reserve(self, labels, declaration, *, task_id=None):
        """Criteria then continuity, in the order dispatch performs them."""
        owner = task_id or self.task_id
        self.store.reserve_turn_criteria(
            owner, validate_criteria(self.criteria_for(*labels)), recorded_at="x"
        )
        return self.store.reserve_turn_continuity(
            owner, validate_declaration(declaration), recorded_at="x"
        )

    def turn(self, labels, declaration, *, task_id=None):
        owner = task_id or self.task_id
        number = self.reserve(labels, declaration, task_id=owner)
        self.store.mark_criteria_dispatch_started(owner, number)
        self.store.mark_continuity_dispatch_started(owner, number)
        with self.sql() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome)"
                " VALUES (?,?,'validation','pwa','x','y','completed')",
                (owner, number),
            )
        return number

    def snap(self, turn, task_id=None):
        return self.store.turn_criteria(task_id or self.task_id, turn).snapshot_id

    def crit(self, turn, task_id=None):
        return [
            item.criterion_id
            for item in self.store.turn_criteria(task_id or self.task_id, turn).criteria
        ]

    def revise(self, labels, predecessor_turn, pairs, *, task_id=None):
        """``pairs`` are ``(current ordinal, predecessor criterion id)``."""
        return self.turn(
            labels,
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(predecessor_turn, task_id),
                "supersedes": [
                    {"criterion_ordinal": ordinal, "predecessor_criterion_id": old}
                    for ordinal, old in pairs
                ],
            },
            task_id=task_id,
        )

    def active(self, turn, task_id=None):
        result = resolve(
            self.store.lineage_inputs(task_id or self.task_id, turn)
        )
        self.assertTrue(result.resolved, getattr(result, "reason", None))
        return [entry.criterion.path for entry in result.active]

    def refusal(self, labels, declaration):
        """Reserve criteria, then assert the continuity declaration is refused.

        Accepts either half of the check and returns its closed reason code: the
        structural one raises :class:`ContinuitySubmissionInvalid` out of
        ``validate_declaration``, the relational one raises
        :class:`ContinuityInvalid` out of the store. Both happen before anything
        durable is written, which is the property under test.
        """
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for(*labels)), recorded_at="x"
        )
        with self.assertRaises(
            (ContinuityInvalid, ContinuitySubmissionInvalid)
        ) as caught:
            self.store.reserve_turn_continuity(
                self.task_id, validate_declaration(declaration), recorded_at="x"
            )
        error = caught.exception
        return getattr(error, "detail", None) or error.reason

    def continuity_rows(self, turn):
        with self.sql() as connection:
            declarations = connection.execute(
                "SELECT COUNT(*) FROM task_turn_criteria_continuity"
                " WHERE task_id = ? AND turn_number = ?",
                (self.task_id, turn),
            ).fetchone()[0]
            relations = connection.execute(
                "SELECT COUNT(*) FROM task_turn_criterion_supersessions"
                " WHERE task_id = ? AND turn_number = ?",
                (self.task_id, turn),
            ).fetchone()[0]
        return declarations, relations


# -- the correction itself ----------------------------------------------------


class InheritedActiveTests(SupersessionCase):
    def test_the_case_pr10_refused_and_pr11_recorded(self):
        """Turn 1 root A, turn 2 extend B, turn 3 revise C supersedes A."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.assertEqual(self.active(2), ["a.py", "b.py"])

        self.revise(["c"], 2, [(1, self.crit(1)[0])])
        self.assertEqual(self.active(3), ["b.py", "c.py"])

    def test_validation_is_against_active_lineage_not_adjacency(self):
        """Multi-extend: A survives two turns and is still retirable."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.turn(["c"], {"mode": "extend", "predecessor_snapshot_id": self.snap(2)})
        self.assertEqual(self.active(3), ["a.py", "b.py", "c.py"])

        self.revise(["d"], 3, [(1, self.crit(1)[0])])
        self.assertEqual(self.active(4), ["b.py", "c.py", "d.py"])

    def test_a_criterion_that_survived_an_earlier_revise_may_be_retired(self):
        """B was never in turn 2's snapshot; it survived turn 2's revision."""
        self.turn(["a", "b"], {"mode": "root"})
        self.revise(["c"], 1, [(1, self.crit(1)[0])])
        self.assertEqual(self.active(2), ["b.py", "c.py"])

        self.revise(["d"], 2, [(1, self.crit(1)[1])])
        self.assertEqual(self.active(3), ["c.py", "d.py"])

    def test_a_direct_predecessor_criterion_still_works(self):
        """The ordinary PR10 case. Widening must not move it."""
        self.turn(["a"], {"mode": "root"})
        self.revise(["b"], 1, [(1, self.crit(1)[0])])
        self.assertEqual(self.active(2), ["b.py"])


class InheritedShapeTests(SupersessionCase):
    """Split, merge and many-to-many, all with inherited old sides."""

    def three_generations(self):
        self.turn(["a", "b"], {"mode": "root"})
        self.turn(["keep"], {"mode": "extend",
                             "predecessor_snapshot_id": self.snap(1)})
        self.assertEqual(self.active(2), ["a.py", "b.py", "keep.py"])

    def test_split_from_an_inherited_criterion(self):
        self.three_generations()
        self.revise(["x", "y"], 2, [(1, self.crit(1)[0]), (2, self.crit(1)[0])])
        self.assertEqual(self.active(3), ["b.py", "keep.py", "x.py", "y.py"])

    def test_merge_of_two_inherited_criteria(self):
        self.three_generations()
        self.revise(["z"], 2, [(1, self.crit(1)[0]), (1, self.crit(1)[1])])
        self.assertEqual(self.active(3), ["keep.py", "z.py"])

    def test_many_to_many_across_snapshots(self):
        """Old sides from turn 1, new sides in turn 3, predecessor turn 2."""
        self.three_generations()
        old_a, old_b = self.crit(1)
        self.revise(
            ["p", "q"], 2, [(1, old_a), (1, old_b), (2, old_a), (2, old_b)]
        )
        self.assertEqual(self.active(3), ["keep.py", "p.py", "q.py"])
        with self.sql() as connection:
            self.assertEqual(
                4,
                connection.execute(
                    "SELECT COUNT(*) FROM task_turn_criterion_supersessions"
                    " WHERE task_id = ? AND turn_number = 3",
                    (self.task_id,),
                ).fetchone()[0],
            )

    def test_relations_never_duplicate_a_current_criterion(self):
        self.three_generations()
        self.revise(["z"], 2, [(1, self.crit(1)[0]), (1, self.crit(1)[1])])
        paths = self.active(3)
        self.assertEqual(paths.count("z.py"), 1)


# -- what stays refused -------------------------------------------------------


class StaleTargetTests(SupersessionCase):
    def test_a_criterion_an_earlier_revise_retired(self):
        self.turn(["a", "keep"], {"mode": "root"})
        self.revise(["b"], 1, [(1, self.crit(1)[0])])
        self.assertEqual(self.active(2), ["keep.py", "b.py"])

        detail = self.refusal(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_NOT_ACTIVE)

    def test_a_stale_refusal_stores_nothing(self):
        self.test_a_criterion_an_earlier_revise_retired()
        self.assertEqual(self.continuity_rows(3), (0, 0))

    def test_a_criterion_cut_away_by_a_replace(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "replace", "predecessor_snapshot_id": self.snap(1)})
        self.assertEqual(self.active(2), ["b.py"])

        detail = self.refusal(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_NOT_ACTIVE)
        self.assertEqual(self.continuity_rows(3), (0, 0))

    def test_the_post_replace_criterion_is_accepted(self):
        """The contrast: what the replace *established* is retirable."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "replace", "predecessor_snapshot_id": self.snap(1)})
        self.revise(["c"], 2, [(1, self.crit(2)[0])])
        self.assertEqual(self.active(3), ["c.py"])


class UnavailablePredecessorTests(SupersessionCase):
    def test_revise_over_an_undeclared_predecessor_is_refused(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], None)
        detail = self.refusal(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(2)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_PREDECESSOR_LINEAGE_UNAVAILABLE)
        self.assertEqual(self.continuity_rows(3), (0, 0))

    def test_revise_over_a_legacy_unknown_predecessor_is_refused(self):
        """A turn from before continuity existed: criteria, but no declaration."""
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for("a")), recorded_at="x"
        )
        with self.sql() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_turns (task_id, turn_number, provider,"
                " source, started_at, completed_at, outcome)"
                " VALUES (?,1,'validation','pwa','x','y','completed')",
                (self.task_id,),
            )
        detail = self.refusal(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_PREDECESSOR_LINEAGE_UNAVAILABLE)

    def test_revise_over_a_malformed_predecessor_lineage_is_refused(self):
        """Turn 2's declaration is corrupted after the fact; turn 3 cannot revise."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        with self.sql() as connection:
            connection.execute(
                "UPDATE task_turn_criteria_continuity"
                " SET current_snapshot_id = 'acs_' || substr(current_snapshot_id, 5)"
                " || 'x' WHERE task_id = ? AND turn_number = 2",
                (self.task_id,),
            )
        detail = self.refusal(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(2)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_PREDECESSOR_LINEAGE_UNAVAILABLE)

    def test_a_revise_is_never_downgraded_to_replace(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], None)
        self.refusal(
            ["c"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(2)[0]}
                ],
            },
        )
        with self.sql() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT mode FROM task_turn_criteria_continuity"
                    " WHERE task_id = ? AND turn_number = 3",
                    (self.task_id,),
                ).fetchone()
            )

    def test_replace_over_an_unavailable_predecessor_is_still_accepted(self):
        """The cut point is untouched: replace never needed the prior set."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], None)
        self.turn(["c"], {"mode": "replace", "predecessor_snapshot_id": self.snap(2)})
        self.assertEqual(self.active(3), ["c.py"])

    def test_extend_over_an_undeclared_predecessor_is_still_accepted(self):
        """`extend` carries no relations, so PR12 gives it nothing to validate.

        The resolver still reports turn 3 as unavailable, which is PR11's answer
        and remains correct: the declaration is well-formed, the lineage is not
        resolvable. PR12 only refuses declarations it can prove wrong.
        """
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], None)
        self.turn(["c"], {"mode": "extend", "predecessor_snapshot_id": self.snap(2)})
        result = resolve(self.store.lineage_inputs(self.task_id, 3))
        self.assertFalse(result.resolved)


class AuthorityTests(SupersessionCase):
    def test_a_criterion_from_another_task(self):
        other = self.make_task()
        self.turn(["foreign"], {"mode": "root"}, task_id=other)
        self.turn(["a"], {"mode": "root"})
        detail = self.refusal(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(1, other)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_UNKNOWN)

    def test_a_criterion_id_that_does_not_exist(self):
        self.turn(["a"], {"mode": "root"})
        detail = self.refusal(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": "acr_" + "z" * 26}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_UNKNOWN)

    def test_a_current_snapshot_criterion_used_as_the_old_side(self):
        """A turn may not retire its own new requirement. Not active anywhere prior."""
        self.turn(["a"], {"mode": "root"})
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for("b", "c")),
            recorded_at="x",
        )
        current = self.crit(2)[1]
        with self.assertRaises(ContinuityInvalid) as caught:
            self.store.reserve_turn_continuity(
                self.task_id,
                validate_declaration(
                    {
                        "mode": "revise",
                        "predecessor_snapshot_id": self.snap(1),
                        "supersedes": [
                            {"criterion_ordinal": 1,
                             "predecessor_criterion_id": current}
                        ],
                    }
                ),
                recorded_at="x",
            )
        self.assertEqual(
            caught.exception.detail, REASON_RELATION_PREDECESSOR_NOT_ACTIVE
        )

    def test_a_current_side_ordinal_outside_the_snapshot(self):
        self.turn(["a"], {"mode": "root"})
        detail = self.refusal(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 99,
                     "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_CURRENT_UNKNOWN)

    def test_a_duplicate_relation_is_still_refused(self):
        self.turn(["a"], {"mode": "root"})
        old = self.crit(1)[0]
        detail = self.refusal(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": old},
                    {"criterion_ordinal": 1, "predecessor_criterion_id": old},
                ],
            },
        )
        self.assertEqual(detail, "continuity_relation_duplicate")

    def test_the_relation_bound_is_unchanged(self):
        from cofferdam.workstation.tasks.continuity import (
            MAX_SUPERSESSIONS_PER_TURN,
        )

        self.assertEqual(MAX_SUPERSESSIONS_PER_TURN, 64)
        self.turn(["a"], {"mode": "root"})
        old = self.crit(1)[0]
        detail = self.refusal(
            ["b"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": old}
                ]
                * 65,
            },
        )
        # Refused for the bound, never trimmed — and refused before the resolver
        # is even consulted, because the structural check runs first.
        self.assertIn(
            detail,
            ("continuity_relation_limit_exceeded", "continuity_relation_duplicate"),
        )

    def test_no_refusal_ever_leaves_a_partial_declaration(self):
        """Every refusal above is atomic. One valid relation plus one bad one."""
        self.turn(["a", "keep"], {"mode": "root"})
        self.revise(["b"], 1, [(1, self.crit(1)[0])])
        detail = self.refusal(
            ["c", "d"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(2),
                # The first is legitimate; the second is the criterion turn 2
                # already retired.
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(1)[1]},
                    {"criterion_ordinal": 2,
                     "predecessor_criterion_id": self.crit(1)[0]},
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_NOT_ACTIVE)
        self.assertEqual(self.continuity_rows(3), (0, 0))


# -- dispatch, freezing and replacement ---------------------------------------


class DispatchTests(SupersessionCase):
    def test_the_adapter_is_never_reached_by_a_refused_declaration(self):
        """Through the real follow-up dispatch path, end to end.

        The service surfaces the store's refusal as
        :class:`~.errors.ContinuityUnrecorded` rather than
        :class:`~.errors.ContinuityInvalid` — a PR10 translation this PR does not
        change. What matters here is unchanged either way: it is raised before
        the adapter is constructed a context, so the worker is never told
        anything, and nothing durable is written.
        """
        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterCapabilities,
            TaskAdapter,
        )

        self.turn(["a", "keep"], {"mode": "root"})
        self.revise(["b"], 1, [(1, self.crit(1)[0])])

        calls = []

        class Watcher(TaskAdapter):
            adapter_id = "validation"
            display_name = "Watcher"

            def capabilities(self):
                return AdapterCapabilities(start=True, followup=True)

            def available(self):
                return True

            def start(self, context):  # pragma: no cover - must never run
                calls.append(context.task_id)
                raise AssertionError("the adapter was reached by a refused declaration")

            def followup(self, context, message):  # pragma: no cover - must not run
                calls.append(context.task_id)
                raise AssertionError("the adapter was reached by a refused declaration")

        from cofferdam.workstation.tasks.projects import load_projects

        # The task has to be accepting follow-ups for the dispatch path to be
        # reachable at all; otherwise the refusal under test would be masked by
        # a lifecycle refusal that proves nothing.
        with self.sql() as connection:
            connection.execute(
                "UPDATE tasks SET state = 'ready_for_followup' WHERE task_id = ?",
                (self.task_id,),
            )
        registry = type(self.adapters)((Watcher(),))
        service = TaskService(
            self.config,
            self.store,
            registry,
            projects=load_projects(self.config, registry.ids()),
        )
        from cofferdam.workstation.tasks.errors import ContinuityUnrecorded

        with self.assertRaises(ContinuityUnrecorded):
            service.send_followup(
                self.task_id,
                "more work",
                criteria=self.criteria_for("c"),
                continuity={
                    "mode": "revise",
                    "predecessor_snapshot_id": self.snap(2),
                    "supersedes": [
                        {"criterion_ordinal": 1,
                         "predecessor_criterion_id": self.crit(1)[0]}
                    ],
                },
            )
        self.assertEqual(calls, [])
        self.assertEqual(self.continuity_rows(3), (0, 0))

    def test_an_accepted_inherited_declaration_is_durable_and_frozen(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        number = self.revise(["c"], 2, [(1, self.crit(1)[0])])

        continuity = self.store.turn_continuity(self.task_id, number)
        self.assertEqual(continuity.mode, "revise")
        self.assertEqual(continuity.relation_count, 1)
        self.assertEqual(
            continuity.relations[0].predecessor_criterion_id, self.crit(1)[0]
        )
        self.assertEqual(continuity.dispatch_state, "dispatch_started")

    def test_a_retry_after_dispatch_started_keeps_the_declaration(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.revise(["c"], 2, [(1, self.crit(1)[0])])
        before = self.store.turn_continuity(self.task_id, 3)

        # A retry of the same reserved turn, submitting something different.
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for("z")), recorded_at="x"
        )
        self.store.reserve_turn_continuity(
            self.task_id,
            validate_declaration(
                {"mode": "replace", "predecessor_snapshot_id": self.snap(2)}
            ),
            recorded_at="x",
        )
        after = self.store.turn_continuity(self.task_id, 3)
        self.assertEqual(before, after)
        self.assertEqual(after.continuity_fingerprint, before.continuity_fingerprint)

    def test_replacing_a_captured_snapshot_revalidates_against_the_new_one(self):
        """The PR10 coupling, still intact and now exercised through PR12's check."""
        self.turn(["a", "keep"], {"mode": "root"})
        # Reserve turn 2 without dispatching, so it stays `captured`.
        self.reserve(["b"], {"mode": "extend",
                             "predecessor_snapshot_id": self.snap(1)})
        first_current = self.crit(2)[0]

        # Replace the captured snapshot, then declare a revise against it.
        self.reserve(
            ["x", "y"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(1),
                "supersedes": [
                    {"criterion_ordinal": 2,
                     "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        continuity = self.store.turn_continuity(self.task_id, 2)
        self.assertEqual(continuity.mode, "revise")
        self.assertEqual(continuity.relation_count, 1)
        # The current side names a criterion of the NEW snapshot, and nothing of
        # the discarded one survives.
        self.assertIn(continuity.relations[0].criterion_id, self.crit(2))
        self.assertNotIn(first_current, self.crit(2))
        with self.sql() as connection:
            orphans = connection.execute(
                "SELECT COUNT(*) FROM task_turn_criterion_supersessions"
                " WHERE current_criterion_id = ?",
                (first_current,),
            ).fetchone()[0]
        self.assertEqual(orphans, 0)


# -- identity and structure ---------------------------------------------------


class FingerprintTests(SupersessionCase):
    def test_a_direct_predecessor_declaration_hashes_exactly_as_before(self):
        """PR12 changes which declarations are accepted, not what they mean."""
        self.turn(["a"], {"mode": "root"})
        old = self.crit(1)[0]
        self.revise(["b"], 1, [(1, old)])
        stored = self.store.turn_continuity(self.task_id, 2)
        expected = continuity_fingerprint(
            "declared",
            "revise",
            self.snap(2),
            self.snap(1),
            ((self.crit(2)[0], old),),
        )
        self.assertEqual(stored.continuity_fingerprint, expected)

    def test_an_inherited_declaration_hashes_by_the_same_construction(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        old = self.crit(1)[0]
        self.revise(["c"], 2, [(1, old)])
        stored = self.store.turn_continuity(self.task_id, 3)
        self.assertEqual(
            stored.continuity_fingerprint,
            continuity_fingerprint(
                "declared", "revise", self.snap(3), self.snap(2),
                ((self.crit(3)[0], old),),
            ),
        )

    def test_the_fingerprint_survives_a_reopen(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.revise(["c"], 2, [(1, self.crit(1)[0])])
        before = self.store.turn_continuity(self.task_id, 3).continuity_fingerprint
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(
            before, reopened.turn_continuity(self.task_id, 3).continuity_fingerprint
        )

    def test_relation_submission_order_does_not_move_it(self):
        self.turn(["a", "b"], {"mode": "root"})
        first, second = self.crit(1)
        forward = continuity_fingerprint(
            "declared", "revise", "acs_x", "acs_y", (("acr_n", first), ("acr_n", second))
        )
        backward = continuity_fingerprint(
            "declared", "revise", "acs_x", "acs_y", (("acr_n", second), ("acr_n", first))
        )
        self.assertEqual(forward, backward)

    def test_no_resolver_output_reaches_the_continuity_fingerprint(self):
        """The declaration is the stored fact; the resolver only authorises it."""
        from cofferdam.workstation.tasks import continuity as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        body = source.split("def continuity_fingerprint(")[1].split("\ndef ")[0]
        for forbidden in (
            "RESOLVER_VERSION", "resolve(", "active_criterion", "resolved_fingerprint",
            "active set",
        ):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertEqual(CONTINUITY_MODEL_VERSION, 1)
        self.assertEqual(RESOLVER_VERSION, 1)


class ConsistencyTests(SupersessionCase):
    """Validation and persistence must see one database state."""

    def test_the_predecessor_is_resolved_inside_the_write_transaction(self):
        """No read-then-write window, because there is no second read.

        If the walk ran on its own snapshot before the write began, a concurrent
        commit between the two could let a declaration be accepted against a
        predecessor lineage that no longer looked that way when the row landed.
        It runs on the write connection, inside the write transaction, so the
        question cannot arise.
        """
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})

        seen = []
        original = self.store._lineage_graph_locked

        def watched(connection, task_id, turn_number):
            seen.append((connection.in_transaction, turn_number))
            return original(connection, task_id, turn_number)

        self.store._lineage_graph_locked = watched
        try:
            self.revise(["c"], 2, [(1, self.crit(1)[0])])
        finally:
            del self.store._lineage_graph_locked

        self.assertEqual(len(seen), 1)
        in_transaction, target = seen[0]
        self.assertTrue(in_transaction, "the walk ran outside the write transaction")
        # And it resolved the predecessor, not the turn being written.
        self.assertEqual(target, 2)

    def test_the_walk_uses_the_stores_own_connection(self):
        """A second connection would be a second snapshot by definition."""
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})

        identities = []
        original = self.store._lineage_graph_locked

        def watched(connection, task_id, turn_number):
            identities.append(id(connection))
            return original(connection, task_id, turn_number)

        self.store._lineage_graph_locked = watched
        try:
            self.revise(["c"], 2, [(1, self.crit(1)[0])])
        finally:
            del self.store._lineage_graph_locked
        self.assertEqual(identities, [id(self.store._connect())])

    def test_a_dispatched_predecessors_criteria_are_immutable(self):
        """The lifecycle invariant the consistency argument also rests on."""
        self.turn(["a", "b"], {"mode": "root"})
        before = self.store.turn_criteria(self.task_id, 1)
        self.assertEqual(before.dispatch_state, "dispatch_started")

        # A reservation for turn 1 can no longer replace it: the next reservation
        # allocates turn 2, because turn 1 has a closed `task_turns` row.
        self.store.reserve_turn_criteria(
            self.task_id, validate_criteria(self.criteria_for("z")), recorded_at="x"
        )
        self.assertEqual(self.store.turn_criteria(self.task_id, 1), before)
        self.assertEqual(
            [item.criterion_id for item in before.criteria], self.crit(1)
        )

    def test_a_dispatched_predecessors_continuity_is_immutable(self):
        self.turn(["a"], {"mode": "root"})
        self.turn(["b"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        before = self.store.turn_continuity(self.task_id, 2)
        self.revise(["c"], 2, [(1, self.crit(1)[0])])
        self.assertEqual(self.store.turn_continuity(self.task_id, 2), before)


class InheritedSupersessionEndToEnd(SupersessionCase):
    """The whole corrected lifecycle in one isolated home."""

    def test_the_whole_walk(self):
        from cofferdam.workstation.tasks.adapters.protocol import (
            AdapterCapabilities,
            TaskAdapter,
        )

        # 1. No schema movement. PR11/PR12 add no schema of their own, so this
        # floors at the version continuity arrived on rather than pinning the
        # current one — a later bump belongs to the PR that makes it.
        self.assertGreaterEqual(SCHEMA_VERSION, 9)

        # 2-4. root A,B then extend C. Predecessor active = A,B,C.
        self.turn(["a", "b"], {"mode": "root"})
        self.turn(["c"], {"mode": "extend", "predecessor_snapshot_id": self.snap(1)})
        self.assertEqual(self.active(2), ["a.py", "b.py", "c.py"])

        # 5-6. revise D supersedes A — inherited from turn 1, accepted.
        self.revise(["d"], 2, [(1, self.crit(1)[0])])

        # 7. The declaration is durable and frozen, as PR10 requires.
        declaration = self.store.turn_continuity(self.task_id, 3)
        self.assertEqual(declaration.mode, "revise")
        self.assertEqual(declaration.dispatch_state, "dispatch_started")
        self.assertEqual(declaration.relation_count, 1)

        # 8. Resolver after the turn.
        self.assertEqual(self.active(3), ["b.py", "c.py", "d.py"])

        # 9-11. revise E supersedes B, which originated in turn 1.
        self.revise(["e"], 3, [(1, self.crit(1)[1])])
        self.assertEqual(self.active(4), ["c.py", "d.py", "e.py"])

        # 12. A stale attempt at A is refused before any adapter is reached.
        detail = self.refusal(
            ["x"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(4),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(1)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_NOT_ACTIVE)
        self.assertEqual(self.continuity_rows(5), (0, 0))

        # 13-14. replace F cuts everything away.
        old_c = self.crit(2)[0]
        self.turn(["f"], {"mode": "replace", "predecessor_snapshot_id": self.snap(4)})
        self.assertEqual(self.active(5), ["f.py"])

        # 15. C was cut away, so it may not be superseded.
        detail = self.refusal(
            ["g"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(5),
                "supersedes": [
                    {"criterion_ordinal": 1, "predecessor_criterion_id": old_c}
                ],
            },
        )
        self.assertEqual(detail, REASON_RELATION_PREDECESSOR_NOT_ACTIVE)

        # 16. What the replace established is retirable.
        self.revise(["g"], 5, [(1, self.crit(5)[0])])
        self.assertEqual(self.active(6), ["g.py"])

        # 17. not_declared predecessor plus revise is refused.
        self.turn(["h"], None)
        detail = self.refusal(
            ["i"],
            {
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(7),
                "supersedes": [
                    {"criterion_ordinal": 1,
                     "predecessor_criterion_id": self.crit(7)[0]}
                ],
            },
        )
        self.assertEqual(detail, REASON_PREDECESSOR_LINEAGE_UNAVAILABLE)

        # 18. ...and a later replace still recovers.
        self.turn(["j"], {"mode": "replace", "predecessor_snapshot_id": self.snap(7)})
        self.assertEqual(self.active(8), ["j.py"])

        # 19-20. No schema change, no new table.
        with self.sql() as connection:
            self.assertEqual(
                SCHEMA_VERSION,
                int(
                    connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                ),
            )
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
        for name in tables:
            for forbidden in ("active", "lineage", "resolv"):
                self.assertNotIn(forbidden, name.lower())

        # 21-22. No aggregate anywhere in the result, and no runner.
        result = resolve(self.store.lineage_inputs(self.task_id, 8))
        for field in result.__dataclass_fields__:
            self.assertNotIn("met", field.lower())
            self.assertNotIn("verdict", field.lower())
        self.assertFalse(hasattr(result, "all_met"))
        self.assertEqual(RESOLVER_VERSION, 1)
        self.assertEqual(CONTINUITY_MODEL_VERSION, 1)

        # 23. Adapters were never involved in any of it.
        self.assertTrue(issubclass(TaskAdapter, object))
        self.assertIsInstance(AdapterCapabilities(), AdapterCapabilities)



class OneAlgorithmTests(unittest.TestCase):
    """There must be exactly one definition of what "active" means."""

    def store_source(self):
        return (
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "store.py"
        ).read_text(encoding="utf-8")

    def reservation_body(self):
        source = self.store_source()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "reserve_turn_continuity"
            ):
                return node
        raise AssertionError("reserve_turn_continuity not found")

    def test_the_validation_calls_the_pure_resolver(self):
        called = {
            node.func.id
            for node in ast.walk(self.reservation_body())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("resolve", called)

    def test_the_validation_reuses_the_shared_lineage_fetch(self):
        attributes = {
            node.func.attr
            for node in ast.walk(self.reservation_body())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("_lineage_graph_locked", attributes)

    def test_the_store_does_not_reimplement_the_mode_algorithm(self):
        """No root/extend/replace/revise branching outside the resolver."""
        from cofferdam.workstation.tasks import lineage

        body = ast.unparse(self.reservation_body())
        # `root` and `revise` are legitimately named — one is validated
        # structurally here, the other selects the new check. `extend` and
        # `replace` have no business in a write-time validator, and their
        # presence would mean the fold had been copied.
        self.assertNotIn("CONTINUITY_EXTEND", body)
        self.assertNotIn("CONTINUITY_REPLACE", body)
        # And the fold itself lives in exactly one module.
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "tasks" / "lineage.py"
        ).read_text(encoding="utf-8")
        self.assertIn("CONTINUITY_EXTEND", source)
        self.assertEqual(lineage.RESOLVER_VERSION, 1)

    def test_only_the_resolver_module_computes_an_active_set(self):
        """Scoped to Task Core. `resolve` is a common name in unrelated packages."""
        definitions = set()
        tasks = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(tasks.rglob("*.py")):
            if path.name == "lineage.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lowered = node.name.lower()
                    if "active_set" in lowered or lowered in (
                        "resolve", "resolve_active", "active_criteria",
                    ):
                        definitions.add("%s:%s" % (path.name, node.name))
        self.assertEqual(definitions, set())


class NegativeSpaceTests(unittest.TestCase):
    def test_the_schema_version_did_not_move(self):
        # PR11/PR12 add no schema of their own; a later bump belongs to the PR
        # that makes it, so this floors rather than pins.
        self.assertGreaterEqual(SCHEMA_VERSION, 9)

    def test_no_semantic_version_moved(self):
        from cofferdam.workstation.tasks.criteria import CRITERIA_MODEL_VERSION
        from cofferdam.workstation.tasks.evaluation import EVALUATOR_VERSION
        from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION

        self.assertEqual(CONTINUITY_MODEL_VERSION, 1)
        self.assertEqual(RESOLVER_VERSION, 1)
        self.assertEqual(CRITERIA_MODEL_VERSION, 1)
        self.assertEqual(EVALUATOR_VERSION, 1)
        self.assertEqual(ASSEMBLER_VERSION, 3)

    def test_no_aggregate_or_runner_appeared(self):
        forbidden = {
            "AGGREGATOR_VERSION", "all_met", "aggregate", "task_verdict",
            "acceptance_outcome", "CheckRunner", "run_check", "check_id",
        }
        for path in sorted((REPO_ROOT / "cofferdam").rglob("*.py")):
            if path.name == "acceptance.py":
                continue  # M2K PR21; see the sole-definer test below
            declared = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    declared.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            declared.add(target.id)
            self.assertEqual(
                set(), declared & forbidden, "%s defines %s" % (path, declared & forbidden)
            )

    def test_no_http_bridge_or_pwa_surface_appeared(self):
        service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        bridge = (REPO_ROOT / "cofferdam" / "actions_bridge" / "service.py").read_text(
            encoding="utf-8"
        )
        pwa = (REPO_ROOT / "web" / "tasks.js").read_text(encoding="utf-8").lower()
        # Matched on the criteria-lineage vocabulary rather than the bare word
        # `continuity`, which the M2J Working Context has used for its own
        # bounded fields since long before this milestone.
        for text, label in ((service, "workstation"), (bridge, "bridge")):
            for forbidden in (
                "supersedes", "predecessor_criterion", "resolve_active_criteria",
                "lineage", "turn_continuity", "criteria_continuity",
            ):
                self.assertNotIn(forbidden, text, "%s: %s" % (label, forbidden))
        for forbidden in ("supersession", "lineage", "continuity", "criterion_ordinal"):
            self.assertNotIn(forbidden, pwa, forbidden)

    def test_the_assessment_response_is_unchanged(self):
        from cofferdam.workstation.tasks import assessment

        text = Path(assessment.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("lineage", "continuity", "active_criteria", "supersession"):
            self.assertNotIn(forbidden, text, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
