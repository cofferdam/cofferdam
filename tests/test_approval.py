"""Record validation, canonical serialization, fold, and time-boundary tests
for the approval-state model (no mint, no I/O beyond the store)."""

import unittest

from cofferdam import approval
from cofferdam.approval import (
    ApprovalError,
    LedgerError,
    canonical_line,
    fold_active,
    parse_entry_line,
    validate_approval_entry,
    validate_consumption_entry,
)

A = "a" * 64
B = "b" * 64
C = "c" * 64
ROOT = "d" * 64
OTHER_ROOT = "e" * 64


def approval_entry(**over):
    base = {
        "entry_type": "approval",
        "schema_version": 1,
        "approval_id": A,
        "bound_hash": B,
        "repo_root_id": ROOT,
        "relative_path": "src/app.py",
        "guard_risk": "low",
        "created_at": 1000,
        "expires_at": 1300,
    }
    base.update(over)
    return base


def consumption_entry(**over):
    base = {
        "entry_type": "consumption",
        "schema_version": 1,
        "approval_id": A,
        "bound_hash": B,
        "consumed_at": 1100,
    }
    base.update(over)
    return base


class CanonicalSerializationTests(unittest.TestCase):
    def test_canonical_line_is_frozen_byte_form(self):
        # Independent hand-written canonical form (sorted keys, no whitespace).
        expected = (
            '{"approval_id":"%s","bound_hash":"%s","created_at":1000,'
            '"entry_type":"approval","expires_at":1300,"guard_risk":"low",'
            '"relative_path":"src/app.py","repo_root_id":"%s","schema_version":1}'
        ) % (A, B, ROOT)
        self.assertEqual(canonical_line(approval_entry()), expected)

    def test_round_trip(self):
        entry = validate_approval_entry(approval_entry())
        self.assertEqual(parse_entry_line(canonical_line(entry)), entry)

    def test_consumption_canonical_line(self):
        expected = (
            '{"approval_id":"%s","bound_hash":"%s","consumed_at":1100,'
            '"entry_type":"consumption","schema_version":1}'
        ) % (A, B)
        self.assertEqual(canonical_line(consumption_entry()), expected)


class ApprovalValidationTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_approval_entry(approval_entry())["approval_id"], A)

    def test_unknown_key_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(extra=1))

    def test_missing_key_rejected(self):
        entry = approval_entry()
        del entry["bound_hash"]
        with self.assertRaises(LedgerError):
            validate_approval_entry(entry)

    def test_wrong_type_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(created_at="1000"))

    def test_bool_as_int_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(schema_version=True))

    def test_bad_hash_format_rejected(self):
        for bad in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, ""):
            with self.assertRaises(LedgerError):
                validate_approval_entry(approval_entry(approval_id=bad))

    def test_unknown_schema_version_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(schema_version=2))

    def test_bad_risk_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(guard_risk="critical"))

    def test_expires_not_after_created_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(created_at=1000, expires_at=1000))

    def test_relative_path_traversal_rejected(self):
        for bad in ("../etc/passwd", "/abs/path", "a\\b", "C:/x", "a/../b", "a\x00b"):
            with self.assertRaises(LedgerError):
                validate_approval_entry(approval_entry(relative_path=bad))

    def test_relative_path_control_char_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(relative_path="a\x1bb"))

    def test_oversized_relative_path_rejected(self):
        with self.assertRaises(LedgerError):
            validate_approval_entry(approval_entry(relative_path="a" * 5000))


class ConsumptionValidationTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_consumption_entry(consumption_entry())["approval_id"], A)

    def test_unknown_key_rejected(self):
        with self.assertRaises(LedgerError):
            validate_consumption_entry(consumption_entry(repo_root_id=ROOT))

    def test_wrong_entry_type_rejected(self):
        with self.assertRaises(LedgerError):
            validate_consumption_entry(consumption_entry(entry_type="approval"))


class ParseLineTests(unittest.TestCase):
    def test_unknown_entry_type_rejected(self):
        with self.assertRaises(LedgerError):
            parse_entry_line('{"entry_type":"mystery","schema_version":1}')

    def test_non_object_rejected(self):
        for bad in ("[]", "1", '"x"', "null", ""):
            with self.assertRaises(LedgerError):
                parse_entry_line(bad)

    def test_nan_literal_rejected(self):
        with self.assertRaises(LedgerError):
            parse_entry_line('{"entry_type":"approval","created_at":NaN}')


class FoldTests(unittest.TestCase):
    def _entries(self, *entries):
        return [validate_approval_entry(e) if e["entry_type"] == "approval"
                else validate_consumption_entry(e) for e in entries]

    def test_no_records(self):
        self.assertEqual(fold_active([], ROOT, 1100), {})

    def test_single_active(self):
        active = fold_active(self._entries(approval_entry()), ROOT, 1100)
        self.assertIn(B, active)
        self.assertEqual(active[B].state, "active")

    def test_expired_not_active(self):
        self.assertEqual(fold_active(self._entries(approval_entry()), ROOT, 1300), {})

    def test_rollback_before_created_not_active(self):
        self.assertEqual(fold_active(self._entries(approval_entry()), ROOT, 999), {})

    def test_boundary_created_at_is_active(self):
        self.assertIn(B, fold_active(self._entries(approval_entry()), ROOT, 1000))

    def test_boundary_expires_at_is_expired(self):
        self.assertNotIn(B, fold_active(self._entries(approval_entry()), ROOT, 1300))

    def test_consumed_not_active(self):
        entries = self._entries(approval_entry(), consumption_entry())
        self.assertEqual(fold_active(entries, ROOT, 1100), {})

    def test_foreign_repo_root_filtered(self):
        entries = self._entries(approval_entry(repo_root_id=OTHER_ROOT))
        self.assertEqual(fold_active(entries, ROOT, 1100), {})

    def test_duplicate_approval_id_fails_closed(self):
        entries = self._entries(approval_entry(), approval_entry(bound_hash=C))
        with self.assertRaises(LedgerError):
            fold_active(entries, ROOT, 1100)

    def test_ambiguous_active_same_binding_fails_closed(self):
        entries = self._entries(approval_entry(), approval_entry(approval_id="f" * 64))
        with self.assertRaises(LedgerError):
            fold_active(entries, ROOT, 1100)

    def test_unknown_consumption_fails_closed(self):
        entries = self._entries(approval_entry(), consumption_entry(approval_id="f" * 64))
        with self.assertRaises(LedgerError):
            fold_active(entries, ROOT, 1100)

    def test_duplicate_consumption_idempotent(self):
        entries = self._entries(approval_entry(), consumption_entry(), consumption_entry())
        self.assertEqual(fold_active(entries, ROOT, 1100), {})

    def test_fresh_active_after_expired_history(self):
        # An old approval (expired) plus a fresh active one for the same binding.
        old = approval_entry(approval_id="1" * 64, created_at=1, expires_at=2)
        new = approval_entry(approval_id="2" * 64, created_at=1000, expires_at=1300)
        active = fold_active(self._entries(old, new), ROOT, 1100)
        self.assertEqual(active[B].approval_id, "2" * 64)


if __name__ == "__main__":
    unittest.main()
