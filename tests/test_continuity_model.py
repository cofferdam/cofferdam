"""M2K PR10 — the continuity vocabulary, its validation, and its fingerprint.

Pure model tests: no store, no service, no adapter. What is under examination is
the closed vocabulary PR9 specified and the refusal surface that keeps a caller
from expressing something an aggregate could not compose.

The load-bearing property throughout is that **lineage is declared, never
inferred**. Every test that looks like it is about a string comparison is really
about that: identical text, identical fingerprints, identical paths and identical
ordinals are all things two unrelated criteria can share.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.tasks.continuity import (
    CONTINUITY_DECLARED,
    CONTINUITY_EXTEND,
    CONTINUITY_LEGACY_UNKNOWN,
    CONTINUITY_MODEL_VERSION,
    CONTINUITY_MODES,
    CONTINUITY_NOT_DECLARED,
    CONTINUITY_REPLACE,
    CONTINUITY_REVISE,
    CONTINUITY_ROOT,
    CONTINUITY_STATES,
    MAX_SUPERSESSIONS_PER_TURN,
    REASON_MODE_INVALID,
    REASON_RELATION_DUPLICATE,
    REASON_RELATION_LIMIT_EXCEEDED,
    REASON_RELATIONS_REQUIRED,
    REASON_RELATIONS_UNEXPECTED,
    REASON_ROOT_HAS_PREDECESSOR,
    REASON_SERVER_OWNED_FIELD,
    REASON_UNKNOWN_FIELD,
    STORED_CONTINUITY_STATES,
    ContinuitySubmissionInvalid,
    continuity_fingerprint,
    new_continuity_id,
    new_supersession_id,
    valid_continuity_id,
    valid_supersession_id,
    validate_declaration,
)

SNAP = "acs_" + "a" * 26
SNAP2 = "acs_" + "b" * 26
CRIT = "acr_" + "c" * 26
CRIT2 = "acr_" + "d" * 26


def revise(relations=None, predecessor=SNAP):
    return {
        "mode": CONTINUITY_REVISE,
        "predecessor_snapshot_id": predecessor,
        "supersedes": relations
        if relations is not None
        else [{"criterion_ordinal": 1, "predecessor_criterion_id": CRIT}],
    }


class TheVocabulary(unittest.TestCase):
    def test_three_read_states_and_two_are_stored(self):
        self.assertEqual(
            (CONTINUITY_DECLARED, CONTINUITY_NOT_DECLARED), STORED_CONTINUITY_STATES
        )
        self.assertEqual(
            (CONTINUITY_DECLARED, CONTINUITY_NOT_DECLARED, CONTINUITY_LEGACY_UNKNOWN),
            CONTINUITY_STATES,
        )

    def test_legacy_unknown_is_never_a_stored_state(self):
        """Absence means it. Storing it would make a missing row ambiguous again."""
        self.assertNotIn(CONTINUITY_LEGACY_UNKNOWN, STORED_CONTINUITY_STATES)

    def test_not_declared_is_distinct_from_legacy_unknown(self):
        self.assertNotEqual(CONTINUITY_NOT_DECLARED, CONTINUITY_LEGACY_UNKNOWN)

    def test_exactly_four_modes(self):
        self.assertEqual(
            (CONTINUITY_ROOT, CONTINUITY_EXTEND, CONTINUITY_REPLACE, CONTINUITY_REVISE),
            CONTINUITY_MODES,
        )

    def test_there_is_no_independent_mode(self):
        """It answers neither "prior requirements remain" nor "they do not"."""
        self.assertNotIn("independent", CONTINUITY_MODES)

    def test_no_mode_is_a_task_verdict(self):
        for forbidden in ("pass", "fail", "success", "met", "not_met", "aggregate"):
            self.assertNotIn(forbidden, CONTINUITY_MODES)

    def test_the_model_version_is_one(self):
        self.assertEqual(1, CONTINUITY_MODEL_VERSION)


class NoDeclaration(unittest.TestCase):
    def test_none_is_not_an_error(self):
        """Every caller in this build passes None, and none of them may break."""
        self.assertIsNone(validate_declaration(None))


class ModeValidation(unittest.TestCase):
    def test_a_missing_mode_is_refused(self):
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration({})
        self.assertEqual(REASON_MODE_INVALID, caught.exception.reason)

    def test_an_invented_mode_is_refused(self):
        for mode in ("independent", "latest_wins", "accumulate_all", "preserve", ""):
            with self.assertRaises(ContinuitySubmissionInvalid) as caught:
                validate_declaration({"mode": mode})
            self.assertEqual(REASON_MODE_INVALID, caught.exception.reason)

    def test_root_takes_no_predecessor(self):
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration(
                {"mode": CONTINUITY_ROOT, "predecessor_snapshot_id": SNAP}
            )
        self.assertEqual(REASON_ROOT_HAS_PREDECESSOR, caught.exception.reason)

    def test_root_alone_is_accepted(self):
        declaration = validate_declaration({"mode": CONTINUITY_ROOT})
        self.assertEqual(CONTINUITY_ROOT, declaration.mode)
        self.assertIsNone(declaration.predecessor_snapshot_id)
        self.assertEqual((), declaration.relations)

    def test_the_other_modes_require_a_predecessor(self):
        for mode in (CONTINUITY_EXTEND, CONTINUITY_REPLACE, CONTINUITY_REVISE):
            with self.assertRaises(ContinuitySubmissionInvalid):
                validate_declaration({"mode": mode})

    def test_a_predecessor_must_look_like_a_snapshot_id(self):
        for bad in ("acr_" + "a" * 26, "not-an-id", "", 7, None):
            with self.assertRaises(ContinuitySubmissionInvalid):
                validate_declaration(
                    {"mode": CONTINUITY_EXTEND, "predecessor_snapshot_id": bad}
                )


class RelationRules(unittest.TestCase):
    def test_revise_requires_at_least_one_relation(self):
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration(revise(relations=[]))
        self.assertEqual(REASON_RELATIONS_REQUIRED, caught.exception.reason)

    def test_extend_and_replace_forbid_relations(self):
        for mode in (CONTINUITY_EXTEND, CONTINUITY_REPLACE):
            with self.assertRaises(ContinuitySubmissionInvalid) as caught:
                validate_declaration(
                    {
                        "mode": mode,
                        "predecessor_snapshot_id": SNAP,
                        "supersedes": [
                            {"criterion_ordinal": 1, "predecessor_criterion_id": CRIT}
                        ],
                    }
                )
            self.assertEqual(REASON_RELATIONS_UNEXPECTED, caught.exception.reason)

    def test_root_forbids_relations(self):
        with self.assertRaises(ContinuitySubmissionInvalid):
            validate_declaration(
                {
                    "mode": CONTINUITY_ROOT,
                    "supersedes": [
                        {"criterion_ordinal": 1, "predecessor_criterion_id": CRIT}
                    ],
                }
            )

    def test_a_duplicate_relation_is_refused(self):
        edge = {"criterion_ordinal": 1, "predecessor_criterion_id": CRIT}
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration(revise(relations=[edge, dict(edge)]))
        self.assertEqual(REASON_RELATION_DUPLICATE, caught.exception.reason)

    def test_over_the_bound_is_refused_never_trimmed(self):
        relations = [
            {"criterion_ordinal": n + 1, "predecessor_criterion_id": CRIT}
            for n in range(MAX_SUPERSESSIONS_PER_TURN + 1)
        ]
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration(revise(relations=relations))
        self.assertEqual(REASON_RELATION_LIMIT_EXCEEDED, caught.exception.reason)

    def test_exactly_the_bound_is_accepted(self):
        relations = [
            {"criterion_ordinal": n + 1, "predecessor_criterion_id": CRIT}
            for n in range(MAX_SUPERSESSIONS_PER_TURN)
        ]
        declaration = validate_declaration(revise(relations=relations))
        self.assertEqual(MAX_SUPERSESSIONS_PER_TURN, len(declaration.relations))

    def test_one_old_criterion_may_be_superseded_by_many_new_ones(self):
        """A requirement split in two. Nothing about the relation forbids it."""
        declaration = validate_declaration(
            revise(
                relations=[
                    {"criterion_ordinal": 1, "predecessor_criterion_id": CRIT},
                    {"criterion_ordinal": 2, "predecessor_criterion_id": CRIT},
                ]
            )
        )
        self.assertEqual(2, len(declaration.relations))

    def test_many_old_criteria_may_be_superseded_by_one_new_one(self):
        """Two requirements merged. The same relation, read the other way."""
        declaration = validate_declaration(
            revise(
                relations=[
                    {"criterion_ordinal": 1, "predecessor_criterion_id": CRIT},
                    {"criterion_ordinal": 1, "predecessor_criterion_id": CRIT2},
                ]
            )
        )
        self.assertEqual(2, len(declaration.relations))

    def test_a_relation_names_the_current_side_by_ordinal_not_by_id(self):
        """The caller cannot know a current criterion id: it is minted at reserve."""
        with self.assertRaises(ContinuitySubmissionInvalid):
            validate_declaration(
                revise(
                    relations=[
                        {"criterion_id": CRIT2, "predecessor_criterion_id": CRIT}
                    ]
                )
            )

    def test_a_malformed_ordinal_is_refused(self):
        for bad in (0, -1, "1", 1.0, True, None):
            with self.assertRaises(ContinuitySubmissionInvalid):
                validate_declaration(
                    revise(
                        relations=[
                            {
                                "criterion_ordinal": bad,
                                "predecessor_criterion_id": CRIT,
                            }
                        ]
                    )
                )

    def test_a_malformed_predecessor_criterion_is_refused(self):
        for bad in ("acs_" + "a" * 26, "nope", "", 3, None):
            with self.assertRaises(ContinuitySubmissionInvalid):
                validate_declaration(
                    revise(
                        relations=[
                            {"criterion_ordinal": 1, "predecessor_criterion_id": bad}
                        ]
                    )
                )


class CallerOwnedNothing(unittest.TestCase):
    def test_a_submitted_durable_id_is_refused_by_name(self):
        for field in ("continuity_id", "continuity_fingerprint", "current_snapshot_id"):
            with self.assertRaises(ContinuitySubmissionInvalid) as caught:
                validate_declaration({"mode": CONTINUITY_ROOT, field: "x"})
            self.assertEqual(REASON_SERVER_OWNED_FIELD, caught.exception.reason)

    def test_a_submitted_state_is_refused(self):
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration(
                {"mode": CONTINUITY_ROOT, "continuity_state": CONTINUITY_DECLARED}
            )
        self.assertEqual(REASON_SERVER_OWNED_FIELD, caught.exception.reason)

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        with self.assertRaises(ContinuitySubmissionInvalid) as caught:
            validate_declaration({"mode": CONTINUITY_ROOT, "modee": "root"})
        self.assertEqual(REASON_UNKNOWN_FIELD, caught.exception.reason)

    def test_ids_are_minted_and_well_formed(self):
        self.assertTrue(valid_continuity_id(new_continuity_id()))
        self.assertTrue(valid_supersession_id(new_supersession_id()))
        self.assertNotEqual(new_continuity_id(), new_continuity_id())

    def test_a_non_object_declaration_is_refused(self):
        for bad in ("root", 1, [], (), True):
            with self.assertRaises(ContinuitySubmissionInvalid):
                validate_declaration(bad)


class TheFingerprint(unittest.TestCase):
    def base(self, **kwargs):
        payload = {
            "state": CONTINUITY_DECLARED,
            "mode": CONTINUITY_REVISE,
            "current_snapshot_id": SNAP2,
            "predecessor_snapshot_id": SNAP,
            "relations": ((CRIT2, CRIT),),
        }
        payload.update(kwargs)
        return continuity_fingerprint(
            payload["state"],
            payload["mode"],
            payload["current_snapshot_id"],
            payload["predecessor_snapshot_id"],
            payload["relations"],
        )

    def test_it_is_a_sha256_hexdigest(self):
        self.assertEqual(64, len(self.base()))
        int(self.base(), 16)

    def test_it_is_stable_across_repeated_computation(self):
        self.assertEqual(self.base(), self.base())

    def test_relation_order_does_not_change_it(self):
        """Submission order is not a fact about the lineage."""
        forward = self.base(relations=((CRIT2, CRIT), ("acr_" + "e" * 26, CRIT)))
        reverse = self.base(relations=(("acr_" + "e" * 26, CRIT), (CRIT2, CRIT)))
        self.assertEqual(forward, reverse)

    def test_the_state_changes_it(self):
        self.assertNotEqual(
            self.base(),
            self.base(state=CONTINUITY_NOT_DECLARED, mode=None, relations=()),
        )

    def test_the_mode_changes_it(self):
        self.assertNotEqual(
            self.base(mode=CONTINUITY_EXTEND, relations=()),
            self.base(mode=CONTINUITY_REPLACE, relations=()),
        )

    def test_the_predecessor_snapshot_changes_it(self):
        self.assertNotEqual(
            self.base(), self.base(predecessor_snapshot_id="acs_" + "z" * 26)
        )

    def test_the_current_snapshot_changes_it(self):
        self.assertNotEqual(
            self.base(), self.base(current_snapshot_id="acs_" + "y" * 26)
        )

    def test_the_relation_mapping_changes_it(self):
        self.assertNotEqual(self.base(), self.base(relations=((CRIT2, CRIT2 + ""),)))
        self.assertNotEqual(
            self.base(), self.base(relations=((CRIT2, CRIT), (CRIT, CRIT2)))
        )

    def test_the_relation_count_changes_it(self):
        self.assertNotEqual(self.base(), self.base(relations=()))

    def test_the_model_version_is_bound_into_it(self):
        """A doctrine change must not be readable as the old doctrine."""
        import cofferdam.workstation.tasks.continuity as module

        before = self.base()
        original = module.CONTINUITY_MODEL_VERSION
        module.CONTINUITY_MODEL_VERSION = original + 1
        try:
            self.assertNotEqual(before, self.base())
        finally:
            module.CONTINUITY_MODEL_VERSION = original
        self.assertEqual(before, self.base())

    def test_it_is_domain_separated_from_the_criteria_fingerprint(self):
        from cofferdam.workstation.tasks.criteria import criteria_fingerprint

        self.assertNotEqual(
            continuity_fingerprint(CONTINUITY_NOT_DECLARED, None, None, None, ()),
            criteria_fingerprint("not_provided", ()),
        )

    def test_no_clock_row_id_or_host_path_can_reach_it(self):
        """Everything in it is an argument, and none of these is one."""
        import inspect

        source = inspect.getsource(continuity_fingerprint)
        for forbidden in ("time", "now", "rowid", "recorded_at", "os.", "Path", "random"):
            self.assertNotIn(forbidden + "(", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
