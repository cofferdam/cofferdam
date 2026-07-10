"""No-side-effects and fail-closed guarantees for PR2a.

The trust core must not touch the network or spawn subprocesses. These tests
sabotage ``socket`` and ``subprocess`` so any attempt to use them raises, then
run parsing and path assessment over a batch of hostile inputs and assert
everything is handled cleanly (fail-closed), deterministically, and without a
single write.
"""

import socket
import subprocess
import unittest

from cofferdam.paths import assess_path
from cofferdam.proposal import parse_proposal
from cofferdam.repo_view import PathType
from cofferdam.verdict import Decision

HOSTILE_TARGETS = [
    "../../etc/passwd",
    "/etc/shadow",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "\\\\server\\share\\x",
    ".git/config",
    ".github/workflows/ci.yml",
    "setup.py",
    "a\x00b",
    "file.py:stream",
    "con",
    "a/./../b",
]

HOSTILE_PROPOSALS = [
    None,
    5,
    "not a mapping",
    {"schema_version": 1},
    {"schema_version": 1, "kind": "x", "target_path": "a", "diff": "d", "verdict": "ok"},
    {"schema_version": 2, "kind": "single_file_diff", "target_path": "a", "diff": "d"},
]


class FakeRepoView:
    def path_type(self, parts):
        return PathType.MISSING


class BrokenMapping(dict):
    def keys(self):
        raise RuntimeError("boom")


class NoSideEffectsTests(unittest.TestCase):
    def setUp(self):
        # Sabotage network + subprocess so any use raises loudly.
        self._orig_socket = socket.socket
        self._orig_popen = subprocess.Popen
        self._orig_run = subprocess.run

        def forbidden(*args, **kwargs):
            raise AssertionError("network/subprocess must not be used")

        socket.socket = forbidden
        subprocess.Popen = forbidden
        subprocess.run = forbidden

    def tearDown(self):
        socket.socket = self._orig_socket
        subprocess.Popen = self._orig_popen
        subprocess.run = self._orig_run

    def test_parsing_hostile_input_never_raises(self):
        for raw in HOSTILE_PROPOSALS:
            with self.subTest(raw=raw):
                result = parse_proposal(raw)
                self.assertFalse(result.ok)

    def test_assess_hostile_paths_never_raises_and_blocks(self):
        view = FakeRepoView()
        for target in HOSTILE_TARGETS:
            with self.subTest(target=target):
                result = assess_path(target, view)
                self.assertEqual(result.decision, Decision.BLOCKED)


class FailClosedWrapperTests(unittest.TestCase):
    def test_parse_swallows_internal_exception(self):
        # A mapping whose access explodes must be rejected, not propagated.
        result = parse_proposal(BrokenMapping())
        self.assertFalse(result.ok)
        self.assertIsNone(result.proposal)


class DeterminismUnderBatchTests(unittest.TestCase):
    def test_assess_batch_is_deterministic(self):
        view = FakeRepoView()
        first = [assess_path(t, view) for t in HOSTILE_TARGETS]
        second = [assess_path(t, view) for t in HOSTILE_TARGETS]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
