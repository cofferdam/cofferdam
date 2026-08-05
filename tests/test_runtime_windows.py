"""Window discovery is unavailable here, and says so out loud.

This is the single most important honesty test in the milestone. Everything
else describes something Cofferdam *can* see. This describes something it
cannot, and the only wrong answer is a successful empty list — which would tell
a user staring at three open windows that they have none.

The tests below are deliberately hostile to the easy regression: somebody
implements a backend, it does not work, and the collection quietly becomes
``ok`` with zero items.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from cofferdam.workstation.runtime.models import (
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ResourceCollection,
)
from cofferdam.workstation.runtime.windows import WindowDiscovery

from ._runtime_doubles import FakeSession

RUNTIME_PACKAGE = Path(__file__).resolve().parents[1] / "cofferdam" / "workstation" / "runtime"


class WindowsAreExplicitlyUnavailableTests(unittest.TestCase):
    """(12) Window discovery unavailable is explicit, not a successful empty list."""

    def setUp(self) -> None:
        self.discovery = WindowDiscovery()

    def test_a_live_session_still_reports_unavailable_with_a_reason(self) -> None:
        collection = self.discovery.collect(FakeSession(available=True))

        self.assertEqual(collection.status, STATUS_UNAVAILABLE)
        self.assertNotEqual(collection.status, STATUS_OK)
        self.assertEqual(collection.items, ())
        self.assertTrue(collection.reason)

    def test_the_reason_names_what_was_tried_rather_than_shrugging(self) -> None:
        """A reason a user cannot act on is barely better than no reason."""
        reason = self.discovery.collect(FakeSession()).reason.lower()
        for expected in ("shell", "portal", "accessibility"):
            with self.subTest(expected=expected):
                self.assertIn(expected, reason)

    def test_the_reason_says_cofferdam_will_not_guess(self) -> None:
        reason = self.discovery.collect(FakeSession()).reason.lower()
        self.assertIn("screenshot", reason)
        self.assertIn("extension", reason)

    def test_before_login_the_reason_is_the_missing_session(self) -> None:
        """The more specific truth wins: there are no windows *and* no way to look."""
        collection = self.discovery.collect(
            FakeSession(available=False, reason="no graphical session is active on this host yet")
        )
        self.assertEqual(collection.status, STATUS_UNAVAILABLE)
        self.assertIn("graphical session", collection.reason)

    def test_window_count_for_an_instance_is_none_and_never_zero(self) -> None:
        """Zero is a claim. ``None`` is the absence of one."""
        self.assertIsNone(self.discovery.window_count_for("appinstance-anything"))

    def test_the_evidence_records_the_limitation_machine_readably(self) -> None:
        evidence = self.discovery.collect(FakeSession()).evidence
        self.assertIsNotNone(evidence)
        joined = " ".join(evidence.limitations).lower()
        self.assertIn("not an empty one", joined)


class UnavailableCannotBeFakedTests(unittest.TestCase):
    """Mutation checks: the vocabulary itself refuses the dishonest shapes."""

    def test_an_unavailable_collection_may_not_carry_items(self) -> None:
        with self.assertRaises(ValueError):
            ResourceCollection(
                kind="windows",
                status=STATUS_UNAVAILABLE,
                items=({"resource_id": "window-1"},),
                reason="cannot see windows",
            )

    def test_an_unavailable_collection_must_state_a_reason(self) -> None:
        with self.assertRaises(ValueError):
            ResourceCollection(kind="windows", status=STATUS_UNAVAILABLE)

    def test_an_ok_empty_collection_is_a_different_object_entirely(self) -> None:
        """Proves the two are distinguishable, so the guard above has meaning."""
        empty_ok = ResourceCollection(kind="windows", status=STATUS_OK, items=())
        unavailable = WindowDiscovery().collect(FakeSession())

        self.assertEqual(empty_ok.count if hasattr(empty_ok, "count") else 0, 0)
        self.assertEqual(len(empty_ok.items), len(unavailable.items))
        self.assertNotEqual(empty_ok.status, unavailable.status)
        self.assertIsNone(empty_ok.reason)
        self.assertIsNotNone(unavailable.reason)


class NoForbiddenWindowTechniqueTests(unittest.TestCase):
    """D-2026-08-04-7 at the discovery layer: semantic interfaces only."""

    BANNED = ("Eval", "org.gnome.Shell.Eval", "unsafe-mode")

    def test_no_runtime_module_calls_the_shell_evaluation_endpoint(self) -> None:
        """It is disabled on this host anyway — and would be arbitrary code exec.

        Checked against string constants rather than prose, so the module
        docstring that explains *why* it is refused does not trip the guard.
        """
        offenders = []
        for path in sorted(RUNTIME_PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                if node.value.strip() in self.BANNED:
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [], f"shell evaluation referenced as a call: {offenders}")

    def test_discovery_never_takes_a_screenshot(self) -> None:
        for path in sorted(RUNTIME_PACKAGE.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "take_screenshot":
                    self.fail(f"{path.name}:{node.lineno} captures the screen to discover state")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
