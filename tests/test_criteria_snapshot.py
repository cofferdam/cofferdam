"""M2K PR6 — the criteria model, its bounds, its identity and its fingerprint.

Three properties this module pins, each of which a later change could break
quietly:

**The vocabulary is closed and small.** Two kinds, three evidence predicates,
three operations. Every one of them is something the *already stored* claim,
artifact, worktree-observation and committed-range rows can decide without any
new capture. A predicate that needed evidence this build does not collect would
be a promise the foundation cannot keep.

**Acceptance requirements are never silently reduced.** Everything over a bound
is refused before dispatch rather than trimmed to fit. This is the one place in
M2K where truncation is wrong in a way it is not wrong elsewhere: a bounded
*observation* is honestly `incomplete`, but a bounded *requirement set* reads
afterwards as the complete list of things the work had to do.

**The fingerprint identifies the criteria and nothing else.** Not the row, not
the clock, not the turn, not the host. Same requirements, same value, across a
restart and across machines.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_MODEL_VERSION,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    CRITERIA_STATES,
    CRITERION_ID_PREFIX,
    CRITERION_KINDS,
    CRITERION_OPERATIONS,
    EVIDENCE_PREDICATES,
    EXCLUDED_OPERATIONS,
    FINGERPRINT_CHARS,
    KIND_EVIDENCE,
    KIND_MANUAL,
    MAX_CRITERIA_PER_TURN,
    MAX_CRITERION_DESCRIPTION_CHARS,
    OPERATION_CREATED,
    OPERATION_DELETED,
    OPERATION_MODIFIED,
    PREDICATE_PATH_CHANGED,
    PREDICATE_PATH_OPERATION,
    PREDICATE_RENAME,
    REASON_COMMAND_NOT_SUPPORTED,
    REASON_DESCRIPTION_REQUIRED,
    REASON_DESCRIPTION_TOO_LONG,
    REASON_DUPLICATE,
    REASON_KIND_INVALID,
    REASON_LIMIT_EXCEEDED,
    REASON_OPERATION_INVALID,
    REASON_PATH_DENIED_SENSITIVE,
    REASON_PATH_ESCAPE,
    REASON_PATH_INVALID,
    REASON_PREDICATE_INVALID,
    REASON_SERVER_OWNED_FIELD,
    REASON_SUBMISSION_MALFORMED,
    REASON_UNKNOWN_FIELD,
    SNAPSHOT_ID_PREFIX,
    STORED_CRITERIA_STATES,
    CriteriaSubmissionInvalid,
    criteria_fingerprint,
    criteria_state,
    valid_criterion_id,
    valid_snapshot_id,
    validate_criteria,
)
from cofferdam.workstation.tasks.store import TaskStore

CHANGED = {"kind": "evidence", "predicate": "path_changed", "path": "src/a.py"}
CREATED = {
    "kind": "evidence",
    "predicate": "path_operation",
    "path": "src/b.py",
    "operation": "created",
}
RENAMED = {
    "kind": "evidence",
    "predicate": "rename",
    "path": "src/old.py",
    "to_path": "src/new.py",
}
MANUAL = {"kind": "manual", "description": "a person confirms the page renders"}


def _reason(case, submitted):
    with case.assertRaises(CriteriaSubmissionInvalid) as caught:
        validate_criteria(submitted)
    return caught.exception.reason


class TheVocabulary(unittest.TestCase):
    def test_two_kinds_and_no_more(self):
        self.assertEqual(CRITERION_KINDS, (KIND_EVIDENCE, KIND_MANUAL))

    def test_five_predicates_and_no_more(self):
        """Three turn-change predicates and, since M2K PR17, two state ones.

        Still closed, and the split is the point: the first three ask what a
        worker did during a turn, the last two what the project is at its
        boundary. Nothing converts one into the other.
        """
        from cofferdam.workstation.tasks.criteria import (
            CHANGE_PREDICATES,
            PREDICATE_PATH_ABSENT,
            PREDICATE_PATH_EXISTS,
            STATE_PREDICATES,
        )

        self.assertEqual(
            CHANGE_PREDICATES,
            (PREDICATE_PATH_CHANGED, PREDICATE_PATH_OPERATION, PREDICATE_RENAME),
        )
        self.assertEqual(
            STATE_PREDICATES, (PREDICATE_PATH_EXISTS, PREDICATE_PATH_ABSENT)
        )
        self.assertEqual(EVIDENCE_PREDICATES, CHANGE_PREDICATES + STATE_PREDICATES)

    def test_three_operations_and_no_more(self):
        self.assertEqual(
            CRITERION_OPERATIONS,
            (OPERATION_CREATED, OPERATION_MODIFIED, OPERATION_DELETED),
        )

    def test_renamed_is_not_an_operation(self):
        """A rename is a two-path fact; `operation` carries one path."""
        self.assertIn("renamed", EXCLUDED_OPERATIONS)
        self.assertNotIn("renamed", CRITERION_OPERATIONS)

    def test_the_three_states_and_which_may_be_written(self):
        self.assertEqual(
            CRITERIA_STATES,
            (CRITERIA_PRESENT, CRITERIA_NOT_PROVIDED, CRITERIA_LEGACY_UNKNOWN),
        )
        self.assertEqual(
            STORED_CRITERIA_STATES, (CRITERIA_PRESENT, CRITERIA_NOT_PROVIDED)
        )
        self.assertNotIn(CRITERIA_LEGACY_UNKNOWN, STORED_CRITERIA_STATES)

    def test_no_command_kind_exists(self):
        for forbidden in ("command", "shell", "check", "script", "test", "exec"):
            self.assertNotIn(forbidden, CRITERION_KINDS)


class Accepting(unittest.TestCase):
    def test_none_is_no_criteria_rather_than_an_error(self):
        self.assertEqual(validate_criteria(None), ())
        self.assertEqual(criteria_state(()), CRITERIA_NOT_PROVIDED)

    def test_an_empty_list_is_no_criteria(self):
        self.assertEqual(validate_criteria([]), ())

    def test_the_four_shapes_round_trip(self):
        items = validate_criteria([CHANGED, CREATED, RENAMED, MANUAL])
        self.assertEqual([c.ordinal for c in items], [1, 2, 3, 4])
        self.assertEqual(
            [c.kind for c in items], ["evidence", "evidence", "evidence", "manual"]
        )
        self.assertEqual(items[1].operation, "created")
        self.assertEqual(items[2].to_path, "src/new.py")
        self.assertIsNone(items[3].path)
        self.assertEqual(criteria_state(items), CRITERIA_PRESENT)

    def test_manual_is_not_evidence_evaluable(self):
        items = validate_criteria([CHANGED, MANUAL])
        self.assertTrue(items[0].evidence_evaluable)
        self.assertFalse(items[1].evidence_evaluable)

    def test_an_evidence_criterion_may_carry_a_description(self):
        items = validate_criteria(
            [dict(CHANGED, description="because the caller asked for it")]
        )
        self.assertEqual(items[0].description, "because the caller asked for it")
        # And the structured fields remain the authority: the description does
        # not become a predicate.
        self.assertEqual(items[0].predicate, PREDICATE_PATH_CHANGED)

    def test_ordering_is_the_submission_order_and_is_stored(self):
        forward = validate_criteria([CHANGED, CREATED])
        backward = validate_criteria([CREATED, CHANGED])
        self.assertEqual([c.path for c in forward], ["src/a.py", "src/b.py"])
        self.assertEqual([c.path for c in backward], ["src/b.py", "src/a.py"])
        self.assertEqual([c.ordinal for c in backward], [1, 2])

    def test_a_criterion_draft_has_no_id_yet(self):
        items = validate_criteria([CHANGED])
        self.assertIsNone(items[0].criterion_id)


class Refusing(unittest.TestCase):
    def test_a_bare_mapping_is_not_a_criteria_set(self):
        self.assertEqual(_reason(self, CHANGED), REASON_SUBMISSION_MALFORMED)

    def test_a_string_is_not_a_criteria_set(self):
        self.assertEqual(_reason(self, "everything works"), REASON_SUBMISSION_MALFORMED)

    def test_more_than_the_maximum_is_refused_not_truncated(self):
        submitted = [
            {"kind": "evidence", "predicate": "path_changed", "path": "f%d.py" % n}
            for n in range(MAX_CRITERIA_PER_TURN + 1)
        ]
        self.assertEqual(_reason(self, submitted), REASON_LIMIT_EXCEEDED)
        # And exactly the maximum is accepted, so the bound is the bound.
        self.assertEqual(len(validate_criteria(submitted[:-1])), MAX_CRITERIA_PER_TURN)

    def test_an_oversize_description_is_refused_not_trimmed(self):
        long = "x" * (MAX_CRITERION_DESCRIPTION_CHARS + 1)
        self.assertEqual(
            _reason(self, [{"kind": "manual", "description": long}]),
            REASON_DESCRIPTION_TOO_LONG,
        )
        exact = "x" * MAX_CRITERION_DESCRIPTION_CHARS
        self.assertEqual(
            validate_criteria([{"kind": "manual", "description": exact}])[0].description,
            exact,
        )

    def test_a_manual_criterion_without_a_description_is_refused(self):
        self.assertEqual(
            _reason(self, [{"kind": "manual"}]), REASON_DESCRIPTION_REQUIRED
        )
        self.assertEqual(
            _reason(self, [{"kind": "manual", "description": "   "}]),
            REASON_DESCRIPTION_REQUIRED,
        )

    def test_an_unknown_kind_is_refused(self):
        self.assertEqual(_reason(self, [{"kind": "vibes"}]), REASON_KIND_INVALID)

    def test_an_unknown_predicate_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, predicate="path_matches_regex")]),
            REASON_PREDICATE_INVALID,
        )

    def test_an_invalid_operation_is_refused(self):
        for bad in ("renamed", "changed", "touched", None):
            self.assertEqual(
                _reason(self, [dict(CREATED, operation=bad)]),
                REASON_OPERATION_INVALID,
                bad,
            )

    def test_an_unknown_field_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, severity="high")]), REASON_UNKNOWN_FIELD
        )

    def test_a_caller_supplied_identity_is_refused(self):
        for name in ("criterion_id", "snapshot_id", "ordinal", "criteria_fingerprint"):
            self.assertEqual(
                _reason(self, [dict(CHANGED, **{name: "x"})]),
                REASON_SERVER_OWNED_FIELD,
                name,
            )

    def test_a_duplicate_criterion_is_refused_not_collapsed(self):
        self.assertEqual(_reason(self, [CHANGED, CHANGED]), REASON_DUPLICATE)
        # Two criteria that differ only in description are not duplicates.
        self.assertEqual(
            len(validate_criteria([CHANGED, dict(CHANGED, description="again")])), 2
        )

    def test_a_rename_to_itself_is_refused(self):
        with self.assertRaises(CriteriaSubmissionInvalid):
            validate_criteria([dict(RENAMED, to_path="src/old.py")])

    def test_the_refusal_carries_a_position_and_never_the_value(self):
        with self.assertRaises(CriteriaSubmissionInvalid) as caught:
            validate_criteria([CHANGED, {"kind": "evidence", "path": "/etc/shadow"}])
        self.assertEqual(caught.exception.ordinal, 2)
        self.assertNotIn("shadow", str(caught.exception))
        self.assertNotIn("etc", str(caught.exception))


class Commands(unittest.TestCase):
    """PR6 does not invent dormant execution authority."""

    def test_every_command_shaped_field_is_refused_by_name(self):
        for name in (
            "command",
            "argv",
            "script",
            "shell",
            "cmd",
            "executable",
            "test_command",
            "run",
        ):
            self.assertEqual(
                _reason(self, [dict(CHANGED, **{name: "pytest -q"})]),
                REASON_COMMAND_NOT_SUPPORTED,
                name,
            )

    def test_a_check_id_is_refused_too(self):
        """A future kind may name a host-owned check. This build does not."""
        self.assertEqual(
            _reason(self, [{"kind": "evidence", "check_id": "unit_tests"}]),
            REASON_COMMAND_NOT_SUPPORTED,
        )

    def test_an_expression_string_has_nowhere_to_go(self):
        self.assertEqual(
            _reason(self, [{"kind": "evidence", "expression": "a.py changed AND tests pass"}]),
            REASON_UNKNOWN_FIELD,
        )

    def test_a_command_hidden_in_a_description_stays_inert_text(self):
        """Stored, fingerprinted, never parsed, never a rule. Just a sentence."""
        items = validate_criteria(
            [{"kind": "manual", "description": "run `rm -rf /` to verify"}]
        )
        self.assertEqual(items[0].kind, KIND_MANUAL)
        self.assertIsNone(items[0].predicate)
        self.assertIsNone(items[0].path)


class PathSafety(unittest.TestCase):
    """The same doctrine claims and artifacts already hold to."""

    def test_an_absolute_path_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, path="/etc/passwd")]), REASON_PATH_ESCAPE
        )

    def test_a_traversal_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, path="../../etc/passwd")]), REASON_PATH_ESCAPE
        )

    def test_a_home_relative_path_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, path="~/notes.md")]), REASON_PATH_ESCAPE
        )

    def test_a_windows_drive_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, path="C:/Windows/System32")]),
            REASON_PATH_ESCAPE,
        )

    def test_a_control_character_is_refused(self):
        self.assertEqual(
            _reason(self, [dict(CHANGED, path="src/a\x00.py")]), REASON_PATH_INVALID
        )

    def test_a_sensitive_name_is_refused(self):
        for path in ("secrets/id_rsa", ".env", "config/private.pem", ".env.local"):
            self.assertEqual(
                _reason(self, [dict(CHANGED, path=path)]),
                REASON_PATH_DENIED_SENSITIVE,
                path,
            )

    def test_the_rename_destination_is_held_to_the_same_rule(self):
        self.assertEqual(
            _reason(self, [dict(RENAMED, to_path="/etc/shadow")]), REASON_PATH_ESCAPE
        )
        self.assertEqual(
            _reason(self, [dict(RENAMED, to_path=".env")]), REASON_PATH_DENIED_SENSITIVE
        )

    def test_a_path_is_never_rewritten_into_something_safe(self):
        """Refused, not normalized. `a/../b` is not a claim about `b`."""
        with self.assertRaises(CriteriaSubmissionInvalid):
            validate_criteria([dict(CHANGED, path="src/../src/a.py")])

    def test_no_absolute_root_can_be_stored(self):
        items = validate_criteria([CHANGED, CREATED, RENAMED])
        for criterion in items:
            for value in (criterion.path, criterion.to_path):
                if value is None:
                    continue
                self.assertFalse(value.startswith("/"))
                self.assertFalse(value.startswith("~"))


class TheFingerprint(unittest.TestCase):
    def test_it_is_a_sha256_hex_digest(self):
        value = criteria_fingerprint(CRITERIA_PRESENT, validate_criteria([CHANGED]))
        self.assertEqual(len(value), FINGERPRINT_CHARS)
        int(value, 16)

    def test_it_is_deterministic(self):
        first = criteria_fingerprint(
            CRITERIA_PRESENT, validate_criteria([CHANGED, CREATED, MANUAL])
        )
        second = criteria_fingerprint(
            CRITERIA_PRESENT, validate_criteria([CHANGED, CREATED, MANUAL])
        )
        self.assertEqual(first, second)

    def test_not_provided_has_its_own_stable_value(self):
        first = criteria_fingerprint(CRITERIA_NOT_PROVIDED, ())
        second = criteria_fingerprint(CRITERIA_NOT_PROVIDED, ())
        self.assertEqual(first, second)
        self.assertNotEqual(
            first, criteria_fingerprint(CRITERIA_PRESENT, validate_criteria([CHANGED]))
        )

    def test_every_stored_field_moves_it(self):
        base = validate_criteria([CREATED])
        reference = criteria_fingerprint(CRITERIA_PRESENT, base)
        for variant in (
            [dict(CREATED, path="src/other.py")],
            [dict(CREATED, operation="deleted")],
            [dict(CREATED, description="and it must be tested")],
            [dict(CHANGED)],
            [MANUAL],
        ):
            self.assertNotEqual(
                reference,
                criteria_fingerprint(CRITERIA_PRESENT, validate_criteria(variant)),
                variant,
            )

    def test_reordering_changes_it_because_ordinal_is_stored(self):
        forward = criteria_fingerprint(
            CRITERIA_PRESENT, validate_criteria([CHANGED, CREATED])
        )
        backward = criteria_fingerprint(
            CRITERIA_PRESENT, validate_criteria([CREATED, CHANGED])
        )
        self.assertNotEqual(forward, backward)

    def test_length_prefixing_keeps_adjacent_fields_apart(self):
        """`a` + `b/c` and `a/b` + `c` are different criteria sets."""
        first = criteria_fingerprint(
            CRITERIA_PRESENT,
            validate_criteria(
                [dict(RENAMED, path="a", to_path="b/c")]
            ),
        )
        second = criteria_fingerprint(
            CRITERIA_PRESENT,
            validate_criteria(
                [dict(RENAMED, path="a/b", to_path="c")]
            ),
        )
        self.assertNotEqual(first, second)

    def test_it_is_domain_separated_and_versioned(self):
        from cofferdam.workstation.tasks.criteria import TAG_FINGERPRINT

        self.assertTrue(TAG_FINGERPRINT.startswith(b"cofferdam.criteria"))
        self.assertEqual(CRITERIA_MODEL_VERSION, 1)

    def test_it_does_not_collide_with_the_evidence_bundle_fingerprint(self):
        from cofferdam.workstation.tasks.evidence import TAG_FINGERPRINT as BUNDLE_TAG
        from cofferdam.workstation.tasks.criteria import TAG_FINGERPRINT

        self.assertNotEqual(TAG_FINGERPRINT, BUNDLE_TAG)


class Identity(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr6-id-")
        self.home = Path(self._temp.name)
        from cofferdam.workstation.config import load_config

        config = load_config(self.home)
        config.ensure_dirs()
        self.store = TaskStore(config)
        self.addCleanup(self.store.close)
        self.addCleanup(self._temp.cleanup)
        self.path = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.store.storage_health()
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO tasks (task_id, correlation_id, origin, adapter_id,"
                " project_id, state, created_at, updated_at, prompt, event_cursor)"
                " VALUES ('task_id_x', 'corr', 'pwa', 'validation', 'demo', 'created',"
                " '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z', 'p', 0)"
            )

    def _reserve(self, submitted):
        return self.store.reserve_turn_criteria(
            "task_id_x", validate_criteria(submitted), recorded_at="2026-08-15T00:00:01Z"
        )

    def test_the_snapshot_id_is_server_minted(self):
        self._reserve([CHANGED, MANUAL])
        snapshot = self.store.turn_criteria("task_id_x", 1)
        self.assertTrue(valid_snapshot_id(snapshot.snapshot_id))
        self.assertTrue(snapshot.snapshot_id.startswith(SNAPSHOT_ID_PREFIX))

    def test_every_criterion_id_is_server_minted_and_distinct(self):
        self._reserve([CHANGED, CREATED, MANUAL])
        snapshot = self.store.turn_criteria("task_id_x", 1)
        ids = [c.criterion_id for c in snapshot.criteria]
        self.assertEqual(len(set(ids)), 3)
        for value in ids:
            self.assertTrue(valid_criterion_id(value))
            self.assertTrue(value.startswith(CRITERION_ID_PREFIX))

    def test_a_criterion_id_is_not_derived_from_its_content(self):
        """Two identical criteria on two turns get different ids."""
        self._reserve([CHANGED])
        first = self.store.turn_criteria("task_id_x", 1).criteria[0].criterion_id
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at) VALUES ('task_id_x', 1, 'validation', 'pwa', 'now')"
            )
        self._reserve([CHANGED])
        second = self.store.turn_criteria("task_id_x", 2).criteria[0].criterion_id
        self.assertNotEqual(first, second)

    def test_the_same_criteria_fingerprint_the_same_on_two_turns(self):
        """The id is per row; the fingerprint is about what was asked for."""
        self._reserve([CHANGED, MANUAL])
        first = self.store.turn_criteria("task_id_x", 1).fingerprint
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at) VALUES ('task_id_x', 1, 'validation', 'pwa', 'now')"
            )
        self._reserve([CHANGED, MANUAL])
        second = self.store.turn_criteria("task_id_x", 2).fingerprint
        self.assertEqual(first, second)

    def test_the_snapshot_id_is_unique_across_the_database(self):
        self._reserve([CHANGED])
        first = self.store.turn_criteria("task_id_x", 1).snapshot_id
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "INSERT INTO task_turns (task_id, turn_number, provider, source,"
                " started_at) VALUES ('task_id_x', 1, 'validation', 'pwa', 'now')"
            )
        self._reserve([CHANGED])
        second = self.store.turn_criteria("task_id_x", 2).snapshot_id
        self.assertNotEqual(first, second)

    def test_a_not_provided_snapshot_still_has_identity_and_a_fingerprint(self):
        self._reserve(None)
        snapshot = self.store.turn_criteria("task_id_x", 1)
        self.assertEqual(snapshot.state, CRITERIA_NOT_PROVIDED)
        self.assertTrue(valid_snapshot_id(snapshot.snapshot_id))
        self.assertEqual(len(snapshot.fingerprint), FINGERPRINT_CHARS)
        self.assertEqual(snapshot.criterion_count, 0)
        self.assertEqual(snapshot.criteria, ())
        self.assertTrue(snapshot.recorded)

    def test_stored_order_is_the_ordinal_not_the_rowid(self):
        self._reserve([CREATED, MANUAL, CHANGED])
        snapshot = self.store.turn_criteria("task_id_x", 1)
        self.assertEqual([c.ordinal for c in snapshot.criteria], [1, 2, 3])
        self.assertEqual(
            [c.kind for c in snapshot.criteria], ["evidence", "manual", "evidence"]
        )

    def test_the_fingerprint_survives_a_restart(self):
        self._reserve([CHANGED, CREATED, RENAMED, MANUAL])
        before = self.store.turn_criteria("task_id_x", 1)
        self.store.close()
        from cofferdam.workstation.config import load_config

        config = load_config(self.home)
        reopened = TaskStore(config)
        self.addCleanup(reopened.close)
        after = reopened.turn_criteria("task_id_x", 1)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(before.snapshot_id, after.snapshot_id)
        self.assertEqual(
            criteria_fingerprint(after.state, after.criteria), after.fingerprint
        )

    def test_the_stored_fingerprint_matches_a_recomputation(self):
        self._reserve([CHANGED, CREATED, RENAMED, MANUAL])
        snapshot = self.store.turn_criteria("task_id_x", 1)
        self.assertEqual(
            snapshot.fingerprint,
            criteria_fingerprint(snapshot.state, snapshot.criteria),
        )

    def test_a_missing_turn_reads_legacy_unknown(self):
        snapshot = self.store.turn_criteria("task_id_x", 9)
        self.assertEqual(snapshot.state, CRITERIA_LEGACY_UNKNOWN)
        self.assertFalse(snapshot.recorded)

    def test_the_child_rows_and_the_parent_are_one_transaction(self):
        """A snapshot claiming `present` that can name no criteria is impossible."""
        with sqlite3.connect(str(self.path)) as db:
            parents = db.execute(
                "SELECT criterion_count FROM task_turn_criteria"
            ).fetchall()
        self.assertEqual(parents, [])
        self._reserve([CHANGED, CREATED])
        with sqlite3.connect(str(self.path)) as db:
            count = db.execute(
                "SELECT criterion_count FROM task_turn_criteria"
            ).fetchone()[0]
            items = db.execute(
                "SELECT COUNT(*) FROM task_turn_criterion_items"
            ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual(items, 2)

    def test_a_failing_child_insert_leaves_no_parent(self):
        """Hand-built past the validator, to prove the transaction and not the check."""
        from cofferdam.workstation.tasks.criteria import AcceptanceCriterion

        broken = (
            AcceptanceCriterion(
                ordinal=1, kind="evidence", predicate="path_changed", path="ok.py"
            ),
            # A CHECK violation the validator would never produce: an evidence
            # criterion with no path.
            AcceptanceCriterion(ordinal=2, kind="evidence", predicate="path_changed"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reserve_turn_criteria(
                "task_id_x", broken, recorded_at="2026-08-15T00:00:01Z"
            )
        with sqlite3.connect(str(self.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM task_turn_criteria").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM task_turn_criterion_items"
                ).fetchone()[0],
                0,
            )
        self.assertEqual(
            self.store.turn_criteria("task_id_x", 1).state, CRITERIA_LEGACY_UNKNOWN
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
