"""Store-level tests: persistence, torn/malformed handling, caps, permissions,
path safety, locking/concurrency, and the find/consume integration — against
real temporary repositories."""

import multiprocessing as mp
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cofferdam import approval_store, hashing
from cofferdam.approval import (
    ApprovalError,
    _consume_approval as consume_approval,
    _find_valid_approval as find_valid_approval,
)
from cofferdam.approval_store import _ApprovalStore as ApprovalStore
from cofferdam.repo_view import FilesystemRepoView

from tests._approval_doubles import FakeClock, make_approval_entry, seed_approval

_POSIX = os.name != "nt"


def _can_symlink(tmp: Path) -> bool:
    try:
        target = tmp / "_t"
        link = tmp / "_l"
        target.write_text("x")
        os.symlink(target, link)
        link.unlink()
        target.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def _xproc_consume_worker(root_str, bound_hash, start_at, queue):
    """Top-level (picklable) worker for the cross-process single-use test. Uses
    the SUPPORTED no-DI consume wrapper (its own production clock/store/lock)."""
    import time as _t

    from cofferdam.approval import ApprovalError, consume_approval
    from cofferdam.repo_view import FilesystemRepoView

    delay = start_at - _t.time()
    if delay > 0:
        _t.sleep(delay)
    try:
        consume_approval(bound_hash, FilesystemRepoView(root_str))
        queue.put("ok")
    except ApprovalError:
        queue.put("void")
    except BaseException as exc:  # pragma: no cover - surface unexpected failures
        queue.put("err:" + repr(exc))


class StoreBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.view = FilesystemRepoView(self.root)
        self.root_id = hashing.repo_root_id(self.view.root_bytes())
        self.store = ApprovalStore(self.view)
        self.clock = FakeClock(1100)

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self, **over):
        entry = make_approval_entry(bound_hash="b" * 64, repo_root_id=self.root_id, **over)
        seed_approval(self.store, entry)
        return entry


class LookupConsumeTests(StoreBase):
    def test_no_state_find_returns_none_and_creates_nothing(self):
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )
        self.assertFalse((self.root / ".cofferdam").exists())

    def test_seed_and_find_active(self):
        self.seed()
        view = find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertIsNotNone(view)
        self.assertEqual(view.state, "active")

    def test_find_none_when_expired(self):
        self.seed(created_at=1000, ttl=300)
        self.clock.set(1300)
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_consume_then_replay_void(self):
        self.seed()
        consumed = consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertEqual(consumed.state, "consumed")
        with self.assertRaises(ApprovalError):
            consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_consume_missing_void(self):
        with self.assertRaises(ApprovalError):
            consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_foreign_root_ledger_not_active(self):
        entry = make_approval_entry(bound_hash="b" * 64, repo_root_id="e" * 64)
        seed_approval(self.store, entry)
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )


class ScopeTests(StoreBase):
    def _snapshot_outside_cofferdam(self):
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if ".cofferdam" not in p.relative_to(self.root).parts
        )

    def test_only_cofferdam_written(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("old\n")
        before = self._snapshot_outside_cofferdam()
        self.seed()
        consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertEqual(self._snapshot_outside_cofferdam(), before)

    @unittest.skipUnless(_POSIX, "POSIX permission semantics")
    def test_state_files_have_restrictive_modes(self):
        self.seed()
        dmode = (self.root / ".cofferdam").stat().st_mode & 0o777
        lmode = (self.root / ".cofferdam" / "approvals.jsonl").stat().st_mode & 0o777
        kmode = (self.root / ".cofferdam" / "approvals.lock").stat().st_mode & 0o777
        self.assertEqual(dmode & 0o077, 0)
        self.assertEqual(lmode & 0o177, 0)
        self.assertEqual(kmode & 0o177, 0)


class TornAndMalformedTests(StoreBase):
    def _ledger(self):
        return self.root / ".cofferdam" / "approvals.jsonl"

    def test_empty_ledger_is_valid(self):
        self.seed()
        open(self._ledger(), "w").close()  # truncate to empty
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_complete_newline_terminated_is_valid(self):
        self.seed()
        self.assertIsNotNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_torn_final_approval_line_fails_closed(self):
        self.seed()
        with open(self._ledger(), "a", encoding="utf-8") as fh:
            fh.write('{"entry_type":"approval","schema_ver')  # torn, no newline
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_torn_final_consumption_cannot_permit_second_consume(self):
        # Regression for the balanced-review BLOCKER: a crash during consume can
        # leave a torn (newline-less) consumption line. Under the OLD parser that
        # line was dropped, the approval looked active again, and a SECOND consume
        # would succeed. It must now fail closed.
        self.seed()
        torn = (
            '{"approval_id":"%s","bound_hash":"%s","consumed_at":1100,'
            '"entry_type":"consumption","schema_version":1}'
        ) % ("a" * 64, "b" * 64)
        with open(self._ledger(), "a", encoding="utf-8") as fh:
            fh.write(torn)  # no trailing newline -> torn final record
        with self.assertRaises(ApprovalError):
            consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        # And a plain lookup after the torn write also grants no authority.
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_malformed_middle_line_fails_closed(self):
        self.seed()
        with open(self._ledger(), "a", encoding="utf-8") as fh:
            fh.write("not json\n")  # complete but malformed -> whole ledger invalid
            fh.write('{"entry_type":"consumption"}\n')
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_bad_utf8_fails_closed(self):
        self.seed()
        with open(self._ledger(), "ab") as fh:
            fh.write(b"\xff\xfe\n")
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_oversized_line_fails_closed(self):
        self.seed()
        with open(self._ledger(), "a", encoding="utf-8") as fh:
            fh.write("x" * (approval_store.MAX_LINE_BYTES + 10) + "\n")
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_entry_count_cap_fails_closed(self):
        self.seed()
        original = approval_store.MAX_ENTRIES
        approval_store.MAX_ENTRIES = 1
        self.addCleanup(setattr, approval_store, "MAX_ENTRIES", original)
        with open(self._ledger(), "a", encoding="utf-8") as fh:
            fh.write('{"entry_type":"consumption","schema_version":1,'
                     '"approval_id":"%s","bound_hash":"%s","consumed_at":1}\n'
                     % ("a" * 64, "b" * 64))
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)


class PathSafetyTests(StoreBase):
    def test_cofferdam_as_regular_file_fails_closed(self):
        (self.root / ".cofferdam").write_text("not a dir")
        with self.assertRaises(ApprovalError):
            self.seed()

    @unittest.skipUnless(_POSIX, "POSIX permission semantics")
    def test_broad_ledger_permissions_fail_closed(self):
        self.seed()
        os.chmod(self.root / ".cofferdam" / "approvals.jsonl", 0o644)
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_symlinked_ledger_fails_closed(self):
        if not _can_symlink(self.root):
            self.skipTest("cannot create symlinks on this platform")
        self.seed()
        ledger = self.root / ".cofferdam" / "approvals.jsonl"
        elsewhere = self.root / "elsewhere.jsonl"
        os.replace(ledger, elsewhere)
        os.symlink(elsewhere, ledger)
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)


class LockingTests(StoreBase):
    def test_lock_timeout_fails_closed(self):
        self.seed()
        holder = ApprovalStore(self.view)
        contender = ApprovalStore(self.view, lock_timeout=0.1)
        with holder.lock(create=False):
            with self.assertRaises(ApprovalError):
                with contender.lock(create=False):
                    pass

    def test_concurrent_consume_exactly_one_succeeds(self):
        self.seed()
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            store = ApprovalStore(self.view)
            clock = FakeClock(1100)
            barrier.wait()
            try:
                consume_approval("b" * 64, store=store, repo_view=self.view, clock=clock)
                results.append(True)
            except ApprovalError:
                errors.append(True)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)


class DataIntegrityTests(StoreBase):
    def _append_raw(self, entry, *, consumption=False):
        with self.store.lock(create=True):
            if consumption:
                self.store._append_consumption(entry)
            else:
                self.store._append_approval(entry)

    def test_consumption_bound_hash_mismatch_fails_closed(self):
        self.seed()  # approval_id "a"*64, bound_hash "b"*64
        self._append_raw(
            {
                "entry_type": "consumption",
                "schema_version": 1,
                "approval_id": "a" * 64,
                "bound_hash": "c" * 64,  # mismatch
                "consumed_at": 1100,
            },
            consumption=True,
        )
        with self.assertRaises(ApprovalError):
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_matching_consumption_consumes(self):
        self.seed()
        consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_foreign_pair_does_not_cross_authorize(self):
        # Foreign approval + its matching foreign consumption, plus a local active
        # approval for the same bound_hash. Foreign records are filtered out; only
        # the local approval is authority, and the fold is deterministic.
        self._append_raw(make_approval_entry(bound_hash="b" * 64, repo_root_id="e" * 64, approval_id="9" * 64))
        self._append_raw(
            {
                "entry_type": "consumption",
                "schema_version": 1,
                "approval_id": "9" * 64,
                "bound_hash": "b" * 64,
                "consumed_at": 1100,
            },
            consumption=True,
        )
        self._append_raw(make_approval_entry(bound_hash="b" * 64, repo_root_id=self.root_id, approval_id="a" * 64))
        view = find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertIsNotNone(view)
        self.assertEqual(view.approval_id, "a" * 64)


class WriteRobustnessTests(StoreBase):
    def test_partial_write_completes(self):
        self.seed()
        real_write = os.write
        state = {"n": 0}

        def partial(fd, data):
            state["n"] += 1
            if state["n"] == 1:
                return real_write(fd, bytes(bytearray(data)[:1]))  # 1 byte only
            return real_write(fd, data)

        with mock.patch("cofferdam.approval_store.os.write", side_effect=partial):
            consumed = consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        self.assertEqual(consumed.state, "consumed")
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_zero_byte_write_fails_closed(self):
        self.seed()
        with mock.patch("cofferdam.approval_store.os.write", return_value=0):
            with self.assertRaises(ApprovalError):
                consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)

    def test_write_error_leaves_authority_intact(self):
        self.seed()
        with mock.patch("cofferdam.approval_store.os.write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        # A failed write records no consumption -> the approval is still active.
        self.assertIsNotNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)
        )

    def test_fsync_error_fails_closed(self):
        self.seed()
        with mock.patch("cofferdam.approval_store.os.fsync", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):
                consume_approval("b" * 64, store=self.store, repo_view=self.view, clock=self.clock)


class CrossProcessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.view = FilesystemRepoView(self.root)
        self.root_id = hashing.repo_root_id(self.view.root_bytes())
        self.store = ApprovalStore(self.view)
        self.addCleanup(self._tmp.cleanup)

    def test_two_processes_exactly_one_consumes(self):
        now = int(time.time())
        seed_approval(
            self.store,
            make_approval_entry(bound_hash="b" * 64, repo_root_id=self.root_id, created_at=now - 5, ttl=3600),
        )
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        start_at = time.time() + 1.0  # allow both spawned interpreters to start
        procs = [
            ctx.Process(target=_xproc_consume_worker, args=(str(self.root), "b" * 64, start_at, queue))
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
        for p in procs:
            if p.is_alive():  # pragma: no cover - only on a lock deadlock
                p.terminate()
        for p in procs:
            self.assertFalse(p.is_alive(), "a consumer process hung (possible lock deadlock)")
        results = sorted(queue.get(timeout=5) for _ in range(2))
        self.assertEqual(results, ["ok", "void"], f"expected exactly one success, got {results}")
        # Exactly one consumption persisted; no active approval remains.
        clock = FakeClock(now)
        self.assertIsNone(
            find_valid_approval("b" * 64, store=self.store, repo_view=self.view, clock=clock)
        )
        with self.store.lock(create=False):
            entries = self.store.read_entries()
        consumptions = [e for e in entries if e["entry_type"] == "consumption"]
        self.assertEqual(len(consumptions), 1)


if __name__ == "__main__":
    unittest.main()
