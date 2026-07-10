"""Vocabulary invariants — most importantly, no ALLOWED state — plus the
byte-stable serialization contract the PR3 audit chain will depend on."""

import json
import unittest

from cofferdam.verdict import Decision, ReasonCode, Risk, Verdict


class DecisionTests(unittest.TestCase):
    def test_only_two_decisions_no_allowed(self):
        # The no-ALLOWED invariant: a file edit is only ever blocked or
        # needs-approval. This test fails loudly if an auto-clear state is added.
        self.assertEqual(
            {member.name for member in Decision},
            {"BLOCKED", "NEEDS_APPROVAL"},
        )
        self.assertNotIn("ALLOWED", {member.name for member in Decision})

    def test_risk_levels(self):
        self.assertEqual(
            {member.name for member in Risk},
            {"LOW", "MEDIUM", "HIGH"},
        )


class ReasonCodeTests(unittest.TestCase):
    def test_values_are_unique(self):
        values = [member.value for member in ReasonCode]
        self.assertEqual(len(values), len(set(values)))

    def test_values_are_stable_strings(self):
        # Reason codes may be persisted in the PR3 audit chain; they must be
        # plain strings, not incidental ints.
        for member in ReasonCode:
            self.assertIsInstance(member.value, str)
            self.assertTrue(member.value)


class VerdictSerializationTests(unittest.TestCase):
    def test_canonical_json_is_byte_stable(self):
        verdict = Verdict(
            Decision.BLOCKED,
            Risk.HIGH,
            (ReasonCode.PATH_ABSOLUTE, ReasonCode.DIFF_BINARY),
        )
        first = verdict.to_canonical_json()
        for _ in range(10):
            self.assertEqual(verdict.to_canonical_json(), first)

    def test_reason_order_does_not_change_bytes(self):
        # Serialization sorts reasons, so construction order cannot perturb it.
        a = Verdict(Decision.BLOCKED, Risk.HIGH, (ReasonCode.PATH_ABSOLUTE, ReasonCode.DIFF_BINARY))
        b = Verdict(Decision.BLOCKED, Risk.HIGH, (ReasonCode.DIFF_BINARY, ReasonCode.PATH_ABSOLUTE))
        self.assertEqual(a.to_canonical_json(), b.to_canonical_json())

    def test_canonical_json_shape(self):
        verdict = Verdict(Decision.NEEDS_APPROVAL, Risk.LOW, ())
        text = verdict.to_canonical_json()
        self.assertEqual(text, '{"decision":"needs_approval","reasons":[],"risk":"low"}')
        self.assertEqual(
            json.loads(text),
            {"decision": "needs_approval", "reasons": [], "risk": "low"},
        )

    def test_no_whitespace_and_ascii_only(self):
        verdict = Verdict(Decision.BLOCKED, Risk.HIGH, (ReasonCode.GUARD_INTERNAL_ERROR,))
        text = verdict.to_canonical_json()
        self.assertNotIn(" ", text)
        self.assertTrue(text.isascii())

    def test_verdict_is_immutable(self):
        verdict = Verdict(Decision.BLOCKED, Risk.HIGH, ())
        with self.assertRaises(Exception):
            verdict.decision = Decision.NEEDS_APPROVAL


if __name__ == "__main__":
    unittest.main()
