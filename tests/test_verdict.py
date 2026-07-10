"""Vocabulary invariants — most importantly, no ALLOWED state."""

import unittest

from cofferdam.verdict import Decision, ReasonCode, Risk


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


if __name__ == "__main__":
    unittest.main()
