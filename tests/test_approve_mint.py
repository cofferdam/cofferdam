"""Authority-bearing mint tests for PR3c1 (``approve_cli._mint``).

Exercises the internal, unsupported post-confirmation mint seam directly (the
interactive TTY/confirmation layer is tested in ``test_approve_cli``), plus the
two mandatory real two-process regressions: concurrent minting and first-ever
approval-state creation. The race tests drive the genuine ``_mint`` path — real
``_ApprovalStore`` lock, fold, duplicate check, append, and fsync — never a fake.
"""

import multiprocessing as mp
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cofferdam import hashing
from cofferdam.approval import ApprovalError, _find_valid_approval
from cofferdam.approval_store import _ApprovalStore, LedgerDurabilityError
from cofferdam.approve_cli import _ActiveDuplicate, _StateChanged, _mint
from cofferdam.dryrun import build_dry_run_artifact
from cofferdam.proposal import parse_proposal
from cofferdam.repo_view import FilesystemRepoView

from tests._approval_doubles import (
    FakeClock,
    constant_token_hex,
    failing_token_hex,
    make_approval_entry,
    seed_approval,
)

_DIFF = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
_PROPOSAL = {
    "schema_version": 1,
    "kind": "single_file_diff",
    "target_path": "src/app.py",
    "diff": _DIFF,
}


def _make_repo(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("old\n")


# --- top-level spawn workers (must be importable for spawn) -------------------


def _mint_worker(root_str, start_at, queue):
    """Independently reach the mint seam and race the real lock/append."""
    try:
        import secrets

        from cofferdam.approval_store import _ApprovalStore as Store
        from cofferdam.approve_cli import _ActiveDuplicate as Dup
        from cofferdam.approve_cli import _mint as mint
        from cofferdam.clock import SystemClock
        from cofferdam.dryrun import build_dry_run_artifact as build
        from cofferdam.proposal import parse_proposal as parse
        from cofferdam.repo_view import FilesystemRepoView as View

        view = View(root_str)
        proposal = parse(_PROPOSAL).proposal
        bound = build(proposal, view).bound_hash
        # Both processes start the critical section at ~the same wall-clock time.
        while time.time() < start_at:
            time.sleep(0.005)
        try:
            mint(
                proposal,
                view,
                store=Store(view),
                clock=SystemClock(),
                token_hex=secrets.token_hex,
                expected_bound_hash=bound,
            )
            queue.put("ok")
        except Dup:
            queue.put("dup")
    except Exception as exc:  # pragma: no cover - surfaced via the queue on failure
        queue.put(f"err:{type(exc).__name__}:{exc}")


class MintUnitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_repo(self.root)
        self.view = FilesystemRepoView(self.root)
        self.store = _ApprovalStore(self.view)
        self.clock = FakeClock(1_000_000)
        self.proposal = parse_proposal(_PROPOSAL).proposal
        self.bh = build_dry_run_artifact(self.proposal, self.view).bound_hash
        self.root_id = hashing.repo_root_id(self.view.root_bytes())
        self.addCleanup(self._tmp.cleanup)

    def _found(self):
        return _find_valid_approval(
            self.bh, store=self.store, repo_view=self.view, clock=self.clock
        )

    def test_success_appends_active_approval(self):
        res = _mint(
            self.proposal, self.view, store=self.store, clock=self.clock,
            token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
        )
        self.assertEqual(res.bound_hash, self.bh)
        self.assertEqual(res.created_at, 1_000_000)
        self.assertEqual(res.expires_at, 1_000_000 + 300)  # fixed 300 s TTL
        found = self._found()
        self.assertIsNotNone(found)
        self.assertEqual(found.approval_id, "a" * 64)
        self.assertEqual(found.guard_risk, "low")

    def test_approval_id_is_64_lowercase_hex(self):
        import secrets as real_secrets

        _mint(
            self.proposal, self.view, store=self.store, clock=self.clock,
            token_hex=real_secrets.token_hex, expected_bound_hash=self.bh,
        )
        found = self._found()
        self.assertRegex(found.approval_id, r"\A[0-9a-f]{64}\Z")

    def test_state_change_refuses_and_writes_nothing(self):
        with self.assertRaises(_StateChanged):
            _mint(
                self.proposal, self.view, store=self.store, clock=self.clock,
                token_hex=constant_token_hex("a" * 64),
                expected_bound_hash="0" * 64,  # not what the human saw
            )
        self.assertIsNone(self._found())

    def test_active_duplicate_writes_nothing(self):
        seed_approval(
            self.store,
            make_approval_entry(
                bound_hash=self.bh, repo_root_id=self.root_id,
                approval_id="d" * 64, created_at=1_000_000, ttl=3600,
            ),
        )
        with self.assertRaises(_ActiveDuplicate):
            _mint(
                self.proposal, self.view, store=self.store, clock=self.clock,
                token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
            )
        # Still exactly one approval entry; the seeded one is untouched.
        with self.store.lock(create=False):
            entries = self.store.read_entries()
        approvals = [e for e in entries if e["entry_type"] == "approval"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["approval_id"], "d" * 64)

    def test_fresh_mint_after_expiry(self):
        seed_approval(
            self.store,
            make_approval_entry(
                bound_hash=self.bh, repo_root_id=self.root_id,
                approval_id="e" * 64, created_at=100, ttl=300,  # long expired
            ),
        )
        res = _mint(
            self.proposal, self.view, store=self.store, clock=self.clock,
            token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
        )
        self.assertEqual(res.bound_hash, self.bh)
        self.assertEqual(self._found().approval_id, "a" * 64)

    def test_approval_id_collision_fails_closed_no_retry(self):
        # A different, active binding already uses this id; our new id collides.
        seed_approval(
            self.store,
            make_approval_entry(
                bound_hash="c" * 64, repo_root_id=self.root_id,
                approval_id="a" * 64, created_at=1_000_000, ttl=3600,
            ),
        )
        with self.assertRaises(ApprovalError):
            _mint(
                self.proposal, self.view, store=self.store, clock=self.clock,
                token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
            )
        self.assertIsNone(self._found())

    def test_entropy_failure_fails_closed(self):
        with self.assertRaises(ApprovalError):
            _mint(
                self.proposal, self.view, store=self.store, clock=self.clock,
                token_hex=failing_token_hex, expected_bound_hash=self.bh,
            )
        self.assertIsNone(self._found())

    def test_partial_write_completes(self):
        real_write = os.write
        state = {"n": 0}

        def partial(fd, data):
            state["n"] += 1
            if state["n"] == 1:
                return real_write(fd, bytes(bytearray(data)[:1]))
            return real_write(fd, data)

        with mock.patch("cofferdam.approval_store.os.write", side_effect=partial):
            _mint(
                self.proposal, self.view, store=self.store, clock=self.clock,
                token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
            )
        self.assertIsNotNone(self._found())

    def test_fsync_failure_after_complete_write_is_indeterminate(self):
        # Pre-create dir + lock so the ONLY fsync reached during the mint is the
        # ledger append's (the record is fully written, then fsync fails).
        with self.store.lock(create=True):
            pass
        with mock.patch("cofferdam.approval_store.os.fsync", side_effect=OSError("fsync")):
            with self.assertRaises(LedgerDurabilityError):
                _mint(
                    self.proposal, self.view, store=self.store, clock=self.clock,
                    token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
                )
        # LedgerDurabilityError is still an OSError (back-compatible on the write
        # path) but is distinguishable, and the record IS discoverable — the
        # durability is indeterminate, not "nothing recorded".
        self.assertTrue(issubclass(LedgerDurabilityError, OSError))
        self.assertIsNotNone(self._found())

    def test_prewrite_failure_is_not_indeterminate(self):
        # A zero-byte (torn) write fails BEFORE the record is complete: it must
        # raise the ordinary ApprovalError, never the durability-indeterminate
        # type, and leave nothing usable.
        with mock.patch("cofferdam.approval_store.os.write", return_value=0):
            with self.assertRaises(ApprovalError) as ctx:
                _mint(
                    self.proposal, self.view, store=self.store, clock=self.clock,
                    token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
                )
        self.assertNotIsInstance(ctx.exception, LedgerDurabilityError)
        self.assertIsNone(self._found())

    def test_ledger_persists_no_patch_or_content(self):
        _mint(
            self.proposal, self.view, store=self.store, clock=self.clock,
            token_hex=constant_token_hex("a" * 64), expected_bound_hash=self.bh,
        )
        text = (self.root / ".cofferdam" / "approvals.jsonl").read_text(encoding="utf-8")
        for marker in ("@@", "---", "+++", "old", "new", "app.py"):
            # relative_path is stored, so app.py IS present — exclude it from the
            # patch-content markers we forbid.
            if marker == "app.py":
                continue
            self.assertNotIn(marker, text)


class MintConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run_race(self, pre_create_state: bool):
        _make_repo(self.root)
        if pre_create_state:
            # Force the ledger/dir to already exist so the race is purely on mint.
            view = FilesystemRepoView(self.root)
            store = _ApprovalStore(view)
            with store.lock(create=True):
                pass  # creates .cofferdam/ + lock, no approval
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        start_at = time.time() + 1.0
        procs = [
            ctx.Process(target=_mint_worker, args=(str(self.root), start_at, queue))
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
        for p in procs:
            if p.is_alive():  # pragma: no cover - only on a lock deadlock
                p.terminate()
            self.assertFalse(p.is_alive(), "a mint process hung")
        results = sorted(queue.get(timeout=5) for _ in range(2))
        self.assertEqual(results, ["dup", "ok"], f"expected exactly one mint, got {results}")
        # Exactly one active approval remains and the ledger is parseable.
        view = FilesystemRepoView(self.root)
        store = _ApprovalStore(view)
        with store.lock(create=False):
            entries = store.read_entries()
        approvals = [e for e in entries if e["entry_type"] == "approval"]
        self.assertEqual(len(approvals), 1)

    def test_two_processes_exactly_one_mints(self):
        self._run_race(pre_create_state=True)

    def test_two_processes_first_ever_state_creation(self):
        # Repo begins with no .cofferdam/; both processes race the first-ever
        # directory + lock-file creation (Option B) and the mint.
        self.assertFalse((self.root / ".cofferdam").exists())
        self._run_race(pre_create_state=False)
        # A valid, parseable state directory remains.
        self.assertTrue((self.root / ".cofferdam" / "approvals.jsonl").is_file())
        self.assertTrue((self.root / ".cofferdam" / "approvals.lock").is_file())


if __name__ == "__main__":
    unittest.main()
