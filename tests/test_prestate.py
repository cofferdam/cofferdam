"""Pre-state descriptor tests: sentinels, distinctness, determinism."""

import hashlib
import unittest

from cofferdam import hashing
from cofferdam.prestate import PreState, PreStateError, prestate_from_bytes, read_prestate
from cofferdam.repo_view import RepoReadError

from tests._repo_doubles import DIRECTORY, OTHER, SYMLINK, InMemoryRepoView


class PureDescriptorTests(unittest.TestCase):
    def test_absent_vector(self):
        ps = prestate_from_bytes(None)
        self.assertEqual(ps.kind, "absent")
        self.assertEqual(ps.descriptor_fields(), (b"absent",))
        self.assertEqual(
            ps.hash_hex(),
            hashlib.sha256(b"cofferdam.prestate.v1" + (6).to_bytes(8, "big") + b"absent").hexdigest(),
        )

    def test_regular_vector(self):
        ps = prestate_from_bytes(b"content")
        self.assertEqual(ps.kind, "regular")
        self.assertEqual(ps.content_sha256, hashlib.sha256(b"content").digest())

    def test_empty_regular_distinct_from_absent(self):
        empty = prestate_from_bytes(b"")
        absent = prestate_from_bytes(None)
        self.assertEqual(empty.kind, "regular")
        self.assertNotEqual(empty.hash_hex(), absent.hash_hex())

    def test_distinct_contents_distinct_hashes(self):
        self.assertNotEqual(prestate_from_bytes(b"a").hash_hex(), prestate_from_bytes(b"b").hash_hex())

    def test_deterministic(self):
        first = prestate_from_bytes(b"x").hash_hex()
        for _ in range(10):
            self.assertEqual(prestate_from_bytes(b"x").hash_hex(), first)

    def test_unknown_kind_fails_closed(self):
        with self.assertRaises(PreStateError):
            PreState(kind="weird", content_sha256=None).descriptor_fields()


class ReadPreStateTests(unittest.TestCase):
    def test_absent_target(self):
        view = InMemoryRepoView({})
        self.assertEqual(read_prestate(view, ("src", "app.py")).kind, "absent")

    def test_regular_target(self):
        view = InMemoryRepoView({("src", "app.py"): b"hello"})
        ps = read_prestate(view, ("src", "app.py"))
        self.assertEqual(ps.kind, "regular")
        self.assertEqual(ps.content_sha256, hashlib.sha256(b"hello").digest())

    def test_empty_target_is_regular_not_absent(self):
        view = InMemoryRepoView({("empty",): b""})
        self.assertEqual(read_prestate(view, ("empty",)).kind, "regular")

    def test_symlink_fails_closed(self):
        view = InMemoryRepoView({("link",): SYMLINK})
        with self.assertRaises(PreStateError):
            read_prestate(view, ("link",))

    def test_directory_fails_closed(self):
        view = InMemoryRepoView({("dir",): DIRECTORY})
        with self.assertRaises(PreStateError):
            read_prestate(view, ("dir",))

    def test_other_fails_closed(self):
        view = InMemoryRepoView({("dev",): OTHER})
        with self.assertRaises(PreStateError):
            read_prestate(view, ("dev",))


if __name__ == "__main__":
    unittest.main()
