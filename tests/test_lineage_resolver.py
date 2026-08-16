"""M2K PR11 — the resolution rules themselves, over hand-built immutable graphs.

Everything here runs on :func:`~cofferdam.workstation.tasks.lineage.resolve`
directly. No database, no service, no store: the point of the pure boundary is
that the semantics can be stated as arithmetic over frozen records, including the
corrupted shapes a real store would never write.

The store's own reads and the corruption fixtures that go through real SQL live
in ``test_lineage_store.py``; the whole walk in an isolated home lives in
``test_lineage_e2e.py``.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_DECLARED,
    CONTINUITY_EXTEND,
    CONTINUITY_LEGACY_UNKNOWN,
    CONTINUITY_NOT_DECLARED,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
    StoredSupersession,
    TurnContinuity,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    AcceptanceCriterion,
    CriteriaSnapshot,
)
from cofferdam.workstation.tasks.lineage import (
    MAX_LINEAGE_DEPTH,
    REASON_CRITERIA_SNAPSHOT_MISSING,
    REASON_CYCLE_DETECTED,
    REASON_DEPTH_EXCEEDED,
    REASON_DUPLICATE_ACTIVE_CRITERION,
    REASON_LEGACY_UNKNOWN,
    REASON_MALFORMED_LINEAGE,
    REASON_NOT_DECLARED,
    REASON_PREDECESSOR_FOREIGN_TASK,
    REASON_PREDECESSOR_MISSING,
    REASON_PREDECESSOR_NOT_EARLIER,
    REASON_PREDECESSOR_UNAVAILABLE,
    REASON_RELATIONS_MODE_MISMATCH,
    REASON_ROOT_HAS_PREDECESSOR,
    REASON_ROOT_NOT_FIRST,
    REASON_SNAPSHOT_MISMATCH,
    REASON_SUPERSESSION_CURRENT_UNKNOWN,
    REASON_SUPERSESSION_PREDECESSOR_UNKNOWN,
    REASON_SUPERSESSION_TARGET_NOT_ACTIVE,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNAVAILABLE,
    RESOLVER_VERSION,
    LineageGraph,
    LineageNode,
    resolve,
)

TASK = "tsk_lineage"


# -- graph construction -------------------------------------------------------
#
# Deliberately hand-rolled rather than driven through the store: several of the
# shapes below are ones PR10 refuses to write, and a read must still answer
# safely when it meets one in a database somebody restored, edited or corrupted.


def criterion(label, ordinal, turn):
    """One evidence criterion, with an id that reads legibly in a failure."""
    return AcceptanceCriterion(
        ordinal=ordinal,
        kind="evidence",
        predicate="path_changed",
        path="%s.py" % label,
        criterion_id="crt_%s" % label,
        description="turn %d" % turn,
    )


def snapshot(turn, labels, *, snapshot_id=None, state=None, count=None,
             fingerprint=None, criteria=None):
    items = criteria
    if items is None:
        items = tuple(
            criterion(label, index, turn) for index, label in enumerate(labels, start=1)
        )
    resolved_state = state
    if resolved_state is None:
        resolved_state = CRITERIA_PRESENT if items else CRITERIA_NOT_PROVIDED
    return CriteriaSnapshot(
        task_id=TASK,
        turn_number=turn,
        state=resolved_state,
        snapshot_id="snp_t%d" % turn if snapshot_id is None else snapshot_id,
        fingerprint=("f" * 64) if fingerprint is None else fingerprint,
        criterion_count=len(items) if count is None else count,
        dispatch_state="turn_opened",
        criteria=items,
    )


def declared(turn, mode, *, predecessor=None, relations=(), current=None,
             fingerprint=None, relation_count=None):
    stored = tuple(
        StoredSupersession(
            supersession_id="sup_%d_%d" % (turn, index),
            ordinal=index,
            criterion_id=new,
            predecessor_criterion_id=old,
        )
        for index, (new, old) in enumerate(relations, start=1)
    )
    return TurnContinuity(
        task_id=TASK,
        turn_number=turn,
        state=CONTINUITY_DECLARED,
        mode=mode,
        continuity_id="ctn_t%d" % turn,
        current_snapshot_id="snp_t%d" % turn if current is None else current,
        predecessor_snapshot_id=predecessor,
        continuity_fingerprint=("c%d" % turn).ljust(64, "0")
        if fingerprint is None
        else fingerprint,
        relation_count=len(stored) if relation_count is None else relation_count,
        dispatch_state="turn_opened",
        relations=stored,
    )


def undeclared(turn, state=CONTINUITY_NOT_DECLARED):
    return TurnContinuity(
        task_id=TASK,
        turn_number=turn,
        state=state,
        continuity_id=None if state == CONTINUITY_LEGACY_UNKNOWN else "ctn_t%d" % turn,
        current_snapshot_id=None
        if state == CONTINUITY_LEGACY_UNKNOWN
        else "snp_t%d" % turn,
        continuity_fingerprint=None
        if state == CONTINUITY_LEGACY_UNKNOWN
        else ("c%d" % turn).ljust(64, "0"),
    )


def graph(target, *nodes, owners=None, earliest=None, task_id=TASK):
    """A closed input graph from ``(continuity, snapshot)`` pairs."""
    built = {}
    for continuity, snap in nodes:
        built[snap.turn_number] = LineageNode(
            turn_number=snap.turn_number, continuity=continuity, snapshot=snap
        )
    resolved_owners = {}
    if owners is None:
        for node in built.values():
            resolved_owners[node.snapshot.snapshot_id] = (
                task_id,
                node.turn_number,
            )
    else:
        resolved_owners = dict(owners)
    first = earliest
    if first is None and built:
        first = min(built)
    return LineageGraph(
        task_id=task_id,
        target_turn_number=target,
        nodes=built,
        snapshot_owners=resolved_owners,
        earliest_snapshot_turn=first,
    )


def paths(result):
    return [entry.criterion.path for entry in result.active]


def ids(result):
    return list(result.active_criterion_ids)


# -- the four modes -----------------------------------------------------------


class ModeTests(unittest.TestCase):
    def test_root_is_exactly_its_own_snapshot(self):
        result = resolve(
            graph(1, (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])))
        )
        self.assertTrue(result.resolved)
        self.assertEqual(result.state, RESOLUTION_RESOLVED)
        self.assertEqual(paths(result), ["a.py", "b.py"])
        self.assertEqual(result.target_snapshot_id, "snp_t1")
        self.assertEqual(result.resolver_version, RESOLVER_VERSION)

    def test_root_traverses_nothing(self):
        result = resolve(
            graph(1, (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])))
        )
        self.assertEqual([step.turn_number for step in result.lineage], [1])

    def test_extend_is_predecessor_then_current(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["c"])),
            )
        )
        self.assertEqual(paths(result), ["a.py", "b.py", "c.py"])
        self.assertEqual([step.mode for step in result.lineage], ["root", "extend"])

    def test_replace_is_exactly_current_and_cuts_the_chain(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, ["c"])),
            )
        )
        self.assertEqual(paths(result), ["c.py"])
        # The predecessor is an audit fact, not a consumed step: its active set
        # was never traversed, so the trace must not claim it was.
        self.assertEqual([step.turn_number for step in result.lineage], [2])

    def test_revise_removes_superseded_and_appends_current(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b", "c"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_d", "crt_b")],
                    ),
                    snapshot(2, ["d"]),
                ),
            )
        )
        self.assertEqual(paths(result), ["a.py", "c.py", "d.py"])

    def test_a_resolved_empty_set_is_an_answer_and_not_a_success(self):
        result = resolve(
            graph(1, (declared(1, CONTINUITY_ROOT), snapshot(1, [])))
        )
        self.assertTrue(result.resolved)
        self.assertEqual(result.active_count, 0)
        # Nothing in the result says anything about acceptance, and there is no
        # attribute a caller could read as one.
        for forbidden in ("met", "all_met", "passed", "outcome", "verdict", "score"):
            self.assertFalse(hasattr(result, forbidden))

    def test_extend_over_an_empty_current_snapshot_keeps_the_predecessor_set(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, [])),
            )
        )
        self.assertEqual(paths(result), ["a.py", "b.py"])

    def test_replace_over_an_empty_current_snapshot_resolves_empty(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, [])),
            )
        )
        self.assertTrue(result.resolved)
        self.assertEqual(result.active_count, 0)


# -- unknown states -----------------------------------------------------------


class UnknownStateTests(unittest.TestCase):
    def test_current_legacy_unknown_is_unavailable(self):
        result = resolve(
            graph(
                1,
                (undeclared(1, CONTINUITY_LEGACY_UNKNOWN), snapshot(1, ["a"])),
            )
        )
        self.assertFalse(result.resolved)
        self.assertEqual(result.state, RESOLUTION_UNAVAILABLE)
        self.assertEqual(result.reason, REASON_LEGACY_UNKNOWN)
        self.assertIsNone(result.cause)

    def test_current_not_declared_is_unavailable(self):
        result = resolve(graph(1, (undeclared(1), snapshot(1, ["a"]))))
        self.assertEqual(result.reason, REASON_NOT_DECLARED)

    def test_an_unavailable_result_carries_no_partial_active_set(self):
        result = resolve(graph(1, (undeclared(1), snapshot(1, ["a"]))))
        self.assertFalse(hasattr(result, "active"))
        self.assertFalse(hasattr(result, "fingerprint"))

    def test_extend_over_an_undeclared_predecessor_is_unavailable(self):
        result = resolve(
            graph(
                2,
                (undeclared(1), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_UNAVAILABLE)
        self.assertEqual(result.cause, REASON_NOT_DECLARED)
        self.assertEqual(result.at_turn_number, 1)

    def test_revise_over_an_undeclared_predecessor_is_unavailable(self):
        result = resolve(
            graph(
                2,
                (undeclared(1), snapshot(1, ["a"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_b", "crt_a")],
                    ),
                    snapshot(2, ["b"]),
                ),
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_UNAVAILABLE)
        self.assertEqual(result.cause, REASON_NOT_DECLARED)

    def test_extend_over_a_legacy_unknown_predecessor_is_unavailable(self):
        result = resolve(
            graph(
                2,
                (undeclared(1, CONTINUITY_LEGACY_UNKNOWN), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
            )
        )
        self.assertEqual(result.cause, REASON_LEGACY_UNKNOWN)

    def test_replace_over_an_undeclared_predecessor_resolves(self):
        result = resolve(
            graph(
                2,
                (undeclared(1), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
            )
        )
        self.assertTrue(result.resolved)
        self.assertEqual(paths(result), ["b.py"])

    def test_replace_over_a_legacy_unknown_predecessor_resolves(self):
        result = resolve(
            graph(
                2,
                (undeclared(1, CONTINUITY_LEGACY_UNKNOWN), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
            )
        )
        self.assertTrue(result.resolved)
        self.assertEqual(paths(result), ["b.py"])

    def test_an_unknown_segment_does_not_poison_a_later_replace(self):
        """The recovery case, stated end to end.

        Turn 1 is a real root, turn 2 declares nothing, turn 3 replaces. Turn 2
        is permanently unavailable and turn 3 is not, because `replace` says the
        prior requirement set is gone whatever it was.
        """
        nodes = (
            (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
            (undeclared(2), snapshot(2, ["b"])),
            (declared(3, CONTINUITY_REPLACE, predecessor="snp_t2"),
             snapshot(3, ["c"])),
        )
        self.assertFalse(resolve(graph(2, *nodes)).resolved)
        recovered = resolve(graph(3, *nodes))
        self.assertTrue(recovered.resolved)
        self.assertEqual(paths(recovered), ["c.py"])
        self.assertEqual([step.turn_number for step in recovered.lineage], [3])

    def test_extend_after_a_recovering_replace_builds_on_the_new_set(self):
        nodes = (
            (undeclared(1, CONTINUITY_LEGACY_UNKNOWN), snapshot(1, ["a"])),
            (declared(2, CONTINUITY_REPLACE, predecessor="snp_t1"),
             snapshot(2, ["b"])),
            (declared(3, CONTINUITY_EXTEND, predecessor="snp_t2"),
             snapshot(3, ["c"])),
        )
        result = resolve(graph(3, *nodes))
        self.assertEqual(paths(result), ["b.py", "c.py"])
        self.assertEqual([step.turn_number for step in result.lineage], [2, 3])


# -- ordering -----------------------------------------------------------------


class OrderingTests(unittest.TestCase):
    def build(self):
        return (
            (declared(1, CONTINUITY_ROOT), snapshot(1, ["zulu", "alpha", "mike"])),
            (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
             snapshot(2, ["yankee", "bravo"])),
        )

    def test_inherited_order_is_preserved_and_never_sorted(self):
        result = resolve(graph(2, *self.build()))
        self.assertEqual(
            paths(result),
            ["zulu.py", "alpha.py", "mike.py", "yankee.py", "bravo.py"],
        )
        self.assertNotEqual(paths(result), sorted(paths(result)))
        self.assertNotEqual(ids(result), sorted(ids(result)))

    def test_current_criteria_follow_in_stored_ordinal_order(self):
        # Handed to the resolver in the reverse of their stored ordinals, to
        # prove the ordinal is what is read rather than the tuple order.
        reversed_items = tuple(
            reversed(
                (
                    criterion("first", 1, 1),
                    criterion("second", 2, 1),
                    criterion("third", 3, 1),
                )
            )
        )
        result = resolve(
            graph(
                1,
                (declared(1, CONTINUITY_ROOT), snapshot(1, [], criteria=reversed_items)),
            )
        )
        self.assertEqual(paths(result), ["first.py", "second.py", "third.py"])

    def test_superseded_entries_are_removed_in_place(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b", "c", "d"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_new", "crt_b")],
                    ),
                    snapshot(2, ["new"]),
                ),
            )
        )
        # a, c and d keep their relative order; the hole b left is not reordered
        # and nothing is promoted into it.
        self.assertEqual(paths(result), ["a.py", "c.py", "d.py", "new.py"])

    def test_order_survives_three_generations(self):
        result = resolve(
            graph(
                3,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "b"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["c", "d"])),
                (
                    declared(
                        3,
                        CONTINUITY_REVISE,
                        predecessor="snp_t2",
                        relations=[("crt_e", "crt_c")],
                    ),
                    snapshot(3, ["e"]),
                ),
            )
        )
        self.assertEqual(paths(result), ["a.py", "b.py", "d.py", "e.py"])


# -- supersession shapes ------------------------------------------------------


class SupersessionTests(unittest.TestCase):
    def resolve_revise(self, predecessor_labels, current_labels, relations):
        return resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, predecessor_labels)),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=relations,
                    ),
                    snapshot(2, current_labels),
                ),
            )
        )

    def test_one_to_one(self):
        result = self.resolve_revise(["a"], ["b"], [("crt_b", "crt_a")])
        self.assertEqual(paths(result), ["b.py"])

    def test_split_removes_the_old_once_and_adds_both_new_once(self):
        result = self.resolve_revise(
            ["a"], ["b", "c"], [("crt_b", "crt_a"), ("crt_c", "crt_a")]
        )
        self.assertEqual(paths(result), ["b.py", "c.py"])
        self.assertEqual(len(result.active), 2)

    def test_merge_removes_both_old_and_adds_the_new_once(self):
        result = self.resolve_revise(
            ["a", "b"], ["c"], [("crt_c", "crt_a"), ("crt_c", "crt_b")]
        )
        self.assertEqual(paths(result), ["c.py"])
        self.assertEqual(len(result.active), 1)

    def test_many_to_many(self):
        result = self.resolve_revise(
            ["a", "b", "keep"],
            ["c", "d"],
            [
                ("crt_c", "crt_a"),
                ("crt_c", "crt_b"),
                ("crt_d", "crt_a"),
                ("crt_d", "crt_b"),
            ],
        )
        self.assertEqual(paths(result), ["keep.py", "c.py", "d.py"])

    def test_relations_never_duplicate_a_current_criterion(self):
        result = self.resolve_revise(
            ["a", "b"], ["c"], [("crt_c", "crt_a"), ("crt_c", "crt_b")]
        )
        self.assertEqual(ids(result).count("crt_c"), 1)

    def test_a_stale_supersession_target_fails_closed(self):
        """The load-bearing case: historical membership is not active membership.

        ``crt_a`` was retired at turn 2. Turn 3 claims to retire it again. It
        still exists as a row, and it is still part of the walked lineage — but
        it is not in turn 2's resolved active set, so turn 3 does not resolve.
        """
        result = resolve(
            graph(
                3,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "keep"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_b", "crt_a")],
                    ),
                    snapshot(2, ["b"]),
                ),
                (
                    declared(
                        3,
                        CONTINUITY_REVISE,
                        predecessor="snp_t2",
                        relations=[("crt_c", "crt_a")],
                    ),
                    snapshot(3, ["c"]),
                ),
            )
        )
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_SUPERSESSION_TARGET_NOT_ACTIVE)
        self.assertEqual(result.at_turn_number, 3)

    def test_an_old_criterion_that_exists_nowhere_in_the_lineage(self):
        result = self.resolve_revise(["a"], ["b"], [("crt_b", "crt_ghost")])
        self.assertEqual(result.reason, REASON_SUPERSESSION_PREDECESSOR_UNKNOWN)

    def test_a_new_criterion_from_the_wrong_snapshot(self):
        result = self.resolve_revise(["a"], ["b"], [("crt_a", "crt_a")])
        self.assertEqual(result.reason, REASON_SUPERSESSION_CURRENT_UNKNOWN)

    def test_a_stale_target_is_never_silently_skipped(self):
        """The tempting wrong answer, named so it cannot creep back.

        Ignoring the stale edge would produce ``keep, b, c`` — a set no
        declaration asked for. The refusal above is the point.
        """
        result = resolve(
            graph(
                3,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a", "keep"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_b", "crt_a")],
                    ),
                    snapshot(2, ["b"]),
                ),
                (
                    declared(
                        3,
                        CONTINUITY_REVISE,
                        predecessor="snp_t2",
                        relations=[("crt_c", "crt_a")],
                    ),
                    snapshot(3, ["c"]),
                ),
            )
        )
        self.assertFalse(hasattr(result, "active"))


# -- invariants over stored rows ----------------------------------------------


class InvariantTests(unittest.TestCase):
    def test_a_declaration_about_a_different_snapshot(self):
        result = resolve(
            graph(
                1,
                (declared(1, CONTINUITY_ROOT, current="snp_elsewhere"),
                 snapshot(1, ["a"])),
            )
        )
        self.assertEqual(result.reason, REASON_SNAPSHOT_MISMATCH)

    def test_a_missing_criteria_snapshot(self):
        result = resolve(
            graph(
                1,
                (
                    declared(1, CONTINUITY_ROOT),
                    CriteriaSnapshot(
                        task_id=TASK, turn_number=1, state=CRITERIA_LEGACY_UNKNOWN
                    ),
                ),
            )
        )
        self.assertEqual(result.reason, REASON_CRITERIA_SNAPSHOT_MISSING)

    def test_a_snapshot_whose_state_disagrees_with_its_criteria(self):
        result = resolve(
            graph(
                1,
                (declared(1, CONTINUITY_ROOT),
                 snapshot(1, [], state=CRITERIA_PRESENT)),
            )
        )
        self.assertEqual(result.reason, REASON_MALFORMED_LINEAGE)

    def test_a_snapshot_whose_count_disagrees_with_its_criteria(self):
        result = resolve(
            graph(1, (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"], count=7)))
        )
        self.assertEqual(result.reason, REASON_MALFORMED_LINEAGE)

    def test_a_criterion_with_no_id(self):
        nameless = (AcceptanceCriterion(ordinal=1, kind="manual", description="x"),)
        result = resolve(
            graph(
                1,
                (declared(1, CONTINUITY_ROOT), snapshot(1, [], criteria=nameless)),
            )
        )
        self.assertEqual(result.reason, REASON_MALFORMED_LINEAGE)

    def test_a_root_that_names_a_predecessor(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_ROOT, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
            )
        )
        self.assertEqual(result.reason, REASON_ROOT_HAS_PREDECESSOR)

    def test_a_root_that_is_not_the_first_snapshot(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_ROOT), snapshot(2, ["b"])),
            )
        )
        self.assertEqual(result.reason, REASON_ROOT_NOT_FIRST)

    def test_a_malformed_root_is_never_reinterpreted_as_replace(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_ROOT), snapshot(2, ["b"])),
            )
        )
        self.assertFalse(result.resolved)

    def test_a_root_carrying_supersession_relations(self):
        result = resolve(
            graph(
                1,
                (declared(1, CONTINUITY_ROOT, relations=[("crt_a", "crt_x")]),
                 snapshot(1, ["a"])),
            )
        )
        self.assertEqual(result.reason, REASON_RELATIONS_MODE_MISMATCH)

    def test_an_extend_carrying_supersession_relations(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (
                    declared(
                        2,
                        CONTINUITY_EXTEND,
                        predecessor="snp_t1",
                        relations=[("crt_b", "crt_a")],
                    ),
                    snapshot(2, ["b"]),
                ),
            )
        )
        self.assertEqual(result.reason, REASON_RELATIONS_MODE_MISMATCH)

    def test_a_revise_with_no_relations(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (declared(2, CONTINUITY_REVISE, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
            )
        )
        self.assertEqual(result.reason, REASON_RELATIONS_MODE_MISMATCH)

    def test_a_relation_count_that_disagrees_with_its_rows(self):
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])),
                (
                    declared(
                        2,
                        CONTINUITY_REVISE,
                        predecessor="snp_t1",
                        relations=[("crt_b", "crt_a")],
                        relation_count=4,
                    ),
                    snapshot(2, ["b"]),
                ),
            )
        )
        self.assertEqual(result.reason, REASON_MALFORMED_LINEAGE)

    def test_a_predecessor_that_does_not_exist(self):
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_ghost"),
                 snapshot(2, ["b"])),
                owners={},
                earliest=2,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_MISSING)

    def test_a_replace_naming_a_predecessor_that_does_not_exist(self):
        """Cutting the dependency does not mean believing the declaration."""
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_ghost"),
                 snapshot(2, ["b"])),
                owners={},
                earliest=2,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_MISSING)

    def test_a_cross_task_predecessor(self):
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_other"),
                 snapshot(2, ["b"])),
                owners={"snp_other": ("tsk_someone_else", 1), "snp_t2": (TASK, 2)},
                earliest=2,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_FOREIGN_TASK)

    def test_a_replace_naming_a_cross_task_predecessor(self):
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_other"),
                 snapshot(2, ["b"])),
                owners={"snp_other": ("tsk_someone_else", 1), "snp_t2": (TASK, 2)},
                earliest=2,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_FOREIGN_TASK)

    def test_a_predecessor_from_a_later_turn(self):
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t5"),
                 snapshot(2, ["b"])),
                (declared(5, CONTINUITY_ROOT), snapshot(5, ["e"])),
                earliest=2,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_NOT_EARLIER)

    def test_a_replace_naming_a_predecessor_from_a_later_turn(self):
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_REPLACE, predecessor="snp_t5"),
                 snapshot(2, ["b"])),
                (declared(5, CONTINUITY_ROOT), snapshot(5, ["e"])),
                earliest=2,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_NOT_EARLIER)

    def test_a_missing_node_in_a_chain_that_asked_for_one(self):
        result = resolve(
            graph(
                2,
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, ["b"])),
                owners={"snp_t1": (TASK, 1), "snp_t2": (TASK, 2)},
                earliest=1,
            )
        )
        self.assertEqual(result.reason, REASON_PREDECESSOR_UNAVAILABLE)
        self.assertEqual(result.cause, REASON_MALFORMED_LINEAGE)

    def test_an_unknown_mode(self):
        result = resolve(
            graph(1, (declared(1, "annex"), snapshot(1, ["a"])))
        )
        self.assertEqual(result.reason, REASON_MALFORMED_LINEAGE)

    def test_an_unknown_continuity_state(self):
        result = resolve(
            graph(1, (undeclared(1, "somehow_else"), snapshot(1, ["a"])))
        )
        self.assertEqual(result.reason, REASON_MALFORMED_LINEAGE)

    def test_the_same_criterion_id_active_twice(self):
        """Impossible from valid rows, and refused rather than deduplicated."""
        shared = (criterion("same", 1, 2),)
        result = resolve(
            graph(
                2,
                (declared(1, CONTINUITY_ROOT), snapshot(1, [], criteria=shared)),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(2, [], criteria=shared)),
            )
        )
        self.assertEqual(result.reason, REASON_DUPLICATE_ACTIVE_CRITERION)

    def test_nothing_is_repaired(self):
        """Every refusal above leaves the input graph exactly as it was."""
        built = graph(
            1, (declared(1, CONTINUITY_ROOT, current="snp_elsewhere"), snapshot(1, ["a"]))
        )
        before = (
            built.nodes[1].continuity,
            built.nodes[1].snapshot,
            dict(built.snapshot_owners),
        )
        resolve(built)
        self.assertEqual(built.nodes[1].continuity, before[0])
        self.assertEqual(built.nodes[1].snapshot, before[1])
        self.assertEqual(dict(built.snapshot_owners), before[2])


# -- bounded traversal --------------------------------------------------------


class BoundedTraversalTests(unittest.TestCase):
    def chain(self, length):
        nodes = [(declared(1, CONTINUITY_ROOT), snapshot(1, ["c1"]))]
        for turn in range(2, length + 1):
            nodes.append(
                (
                    declared(
                        turn, CONTINUITY_EXTEND, predecessor="snp_t%d" % (turn - 1)
                    ),
                    snapshot(turn, ["c%d" % turn]),
                )
            )
        return nodes

    def test_a_chain_exactly_at_the_bound_resolves(self):
        nodes = self.chain(MAX_LINEAGE_DEPTH)
        result = resolve(graph(MAX_LINEAGE_DEPTH, *nodes))
        self.assertTrue(result.resolved)
        self.assertEqual(result.active_count, MAX_LINEAGE_DEPTH)
        self.assertEqual(len(result.lineage), MAX_LINEAGE_DEPTH)

    def test_a_chain_past_the_bound_is_unavailable_rather_than_truncated(self):
        nodes = self.chain(MAX_LINEAGE_DEPTH + 5)
        result = resolve(graph(MAX_LINEAGE_DEPTH + 5, *nodes))
        self.assertFalse(result.resolved)
        self.assertIn(
            REASON_DEPTH_EXCEEDED, (result.reason, result.cause)
        )

    def test_a_self_referential_link_terminates(self):
        result = resolve(
            graph(
                1,
                (declared(1, CONTINUITY_EXTEND, predecessor="snp_t1"),
                 snapshot(1, ["a"])),
            )
        )
        self.assertFalse(result.resolved)
        self.assertEqual(result.reason, REASON_CYCLE_DETECTED)

    def test_a_two_turn_cycle_terminates(self):
        result = resolve(
            graph(
                5,
                (declared(5, CONTINUITY_EXTEND, predecessor="snp_t2"),
                 snapshot(5, ["e"])),
                (declared(2, CONTINUITY_EXTEND, predecessor="snp_t5"),
                 snapshot(2, ["b"])),
                earliest=2,
            )
        )
        self.assertFalse(result.resolved)
        self.assertEqual(result.cause, REASON_CYCLE_DETECTED)


# -- the negative space -------------------------------------------------------


class NoAggregateTests(unittest.TestCase):
    def test_the_resolver_exposes_no_aggregate_vocabulary(self):
        from cofferdam.workstation.tasks import lineage

        for name in dir(lineage):
            lowered = name.lower()
            for forbidden in (
                "aggregat", "all_met", "verdict", "acceptance_", "acceptanceoutcome",
                "task_result", "taskresult",
            ):
                self.assertNotIn(
                    forbidden, lowered, "%s looks like an aggregate" % name
                )
        # `AcceptanceCriterion` is the PR6 type, re-exported by import. It is a
        # requirement, not a judgement, and this test must not be read as
        # permitting a name that only *contains* it.
        self.assertIn("AcceptanceCriterion", dir(lineage))
        self.assertFalse(
            [n for n in dir(lineage) if "acceptance" in n.lower()
             and n != "AcceptanceCriterion"]
        )

    def test_a_resolved_result_carries_no_evaluation_of_any_kind(self):
        result = resolve(
            graph(1, (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])))
        )
        for field in result.__dataclass_fields__:
            lowered = field.lower()
            for forbidden in ("met", "result", "outcome", "verdict", "score", "pass"):
                self.assertNotIn(forbidden, lowered)

    def test_an_active_entry_carries_no_evaluation_of_any_kind(self):
        result = resolve(
            graph(1, (declared(1, CONTINUITY_ROOT), snapshot(1, ["a"])))
        )
        for field in result.active[0].__dataclass_fields__:
            self.assertNotIn("met", field.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
