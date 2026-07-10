"""Frozen known-vector tests for the domain-separated hashing utilities.

The expected values are recomputed **independently** from first principles with
hardcoded tag literals and an 8-byte big-endian length prefix. If the frozen
serialization contract (tags, prefix width, byte order, ordering) ever changes,
these break — which is the point.
"""

import hashlib
import unittest

from cofferdam import hashing


def lp(field: bytes) -> bytes:
    # Hardcoded 8-byte big-endian prefix (NOT importing the module constant, so
    # a change to the module width is caught here).
    return len(field).to_bytes(8, "big") + field


class LengthPrefixTests(unittest.TestCase):
    def test_prefix_shape(self):
        self.assertEqual(hashing.length_prefix(b""), b"\x00" * 8)
        self.assertEqual(hashing.length_prefix(b"a"), b"\x00" * 7 + b"\x01" + b"a")

    def test_prefix_removes_boundary_ambiguity(self):
        # ("a","bc") vs ("ab","c") must not collide.
        left = hashing.length_prefix(b"a") + hashing.length_prefix(b"bc")
        right = hashing.length_prefix(b"ab") + hashing.length_prefix(b"c")
        self.assertNotEqual(left, right)

    def test_rejects_non_bytes(self):
        with self.assertRaises(TypeError):
            hashing.length_prefix("str")  # type: ignore[arg-type]


class ComponentVectorTests(unittest.TestCase):
    def test_canonical_path_hash(self):
        expected = hashlib.sha256(b"cofferdam.path.v1" + lp(b"/repo/src/app.py")).hexdigest()
        self.assertEqual(hashing.canonical_path_hash(b"/repo/src/app.py"), expected)

    def test_patch_hash(self):
        expected = hashlib.sha256(b"cofferdam.patch.v1" + lp(b"--- a\n+++ b\n")).hexdigest()
        self.assertEqual(hashing.patch_hash(b"--- a\n+++ b\n"), expected)

    def test_repo_root_id(self):
        expected = hashlib.sha256(b"cofferdam.reporoot.v1" + lp(b"/home/u/repo")).hexdigest()
        self.assertEqual(hashing.repo_root_id(b"/home/u/repo"), expected)

    def test_prestate_hash_absent(self):
        expected = hashlib.sha256(b"cofferdam.prestate.v1" + lp(b"absent")).hexdigest()
        self.assertEqual(hashing.prestate_hash([b"absent"]), expected)

    def test_prestate_hash_regular(self):
        digest = hashlib.sha256(b"content").digest()
        expected = hashlib.sha256(
            b"cofferdam.prestate.v1" + lp(b"regular") + lp(digest)
        ).hexdigest()
        self.assertEqual(hashing.prestate_hash([b"regular", digest]), expected)

    def test_bound_hash(self):
        cph, ph, psh, rri = "aa", "bb", "cc", "dd"
        expected = hashlib.sha256(
            b"cofferdam.binding.v1" + lp(b"aa") + lp(b"bb") + lp(b"cc") + lp(b"dd")
        ).hexdigest()
        self.assertEqual(hashing.bound_hash(cph, ph, psh, rri), expected)

    def test_bound_tag_is_binding_not_approval(self):
        # The binding tag must not carry approval semantics (balanced fix 2).
        self.assertEqual(hashing.TAG_BOUND, b"cofferdam.binding.v1")

    def test_bound_hash_frozen_golden(self):
        # A fully-hardcoded end-to-end golden value (belt-and-suspenders freeze).
        self.assertEqual(
            hashing.bound_hash("aa", "bb", "cc", "dd"),
            "d601973204954e559e680279b75943dc69d8998c4261898190285c926d9d6991",
        )


class SensitivityTests(unittest.TestCase):
    def setUp(self):
        self.base = hashing.bound_hash("a", "b", "c", "d")

    def test_path_change_changes_bound(self):
        self.assertNotEqual(self.base, hashing.bound_hash("X", "b", "c", "d"))

    def test_patch_change_changes_bound(self):
        self.assertNotEqual(self.base, hashing.bound_hash("a", "X", "c", "d"))

    def test_prestate_change_changes_bound(self):
        self.assertNotEqual(self.base, hashing.bound_hash("a", "b", "X", "d"))

    def test_repo_root_change_changes_bound(self):
        self.assertNotEqual(self.base, hashing.bound_hash("a", "b", "c", "X"))

    def test_field_reordering_changes_hash(self):
        self.assertNotEqual(
            hashing.bound_hash("a", "b", "c", "d"),
            hashing.bound_hash("b", "a", "c", "d"),
        )

    def test_empty_file_prestate_distinct_from_absent(self):
        empty = hashing.prestate_hash([b"regular", hashlib.sha256(b"").digest()])
        absent = hashing.prestate_hash([b"absent"])
        self.assertNotEqual(empty, absent)


class DeterminismTests(unittest.TestCase):
    def test_repeated_runs_byte_identical(self):
        first = hashing.bound_hash("a", "b", "c", "d")
        for _ in range(20):
            self.assertEqual(hashing.bound_hash("a", "b", "c", "d"), first)


if __name__ == "__main__":
    unittest.main()
