"""Replay protection: the claim, the conflict, the race and the restart.

The store's whole job is to be right when two identical requests arrive at once
and when one of them is a retry of something that already happened. Both of
those are timing questions, so the concurrency test uses real threads rather
than reasoning about the code.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cofferdam.actions_bridge.idempotency import (
    STALE_CLAIM_SECONDS,
    IdempotencyConflict,
    IdempotencyStore,
    RequestInFlight,
    body_digest,
    valid_request_id,
)

DIGEST_A = body_digest({"a": 1, "text": "hello"})
DIGEST_B = body_digest({"a": 2, "text": "different"})


class RequestIdTests(unittest.TestCase):
    def test_a_usable_id_is_accepted(self) -> None:
        for value in (
            "gpt-2026-08-09-create-01",
            "abc12345",
            "A.b:c-d_e12",
            "x" * 64,
        ):
            with self.subTest(value=value):
                self.assertTrue(valid_request_id(value))

    def test_a_hostile_or_unusable_id_is_refused(self) -> None:
        for value in (
            "",
            "short",
            "x" * 65,
            "has space",
            "has/slash",
            "has\\backslash",
            "has?query",
            "has#frag",
            "-leading-dash-is-fine-but-not-first",
            "\n newline",
            None,
            7,
            ["a"],
        ):
            with self.subTest(value=repr(value)):
                self.assertFalse(valid_request_id(value))


class DigestTests(unittest.TestCase):
    def test_the_digest_is_stable_across_key_order(self) -> None:
        self.assertEqual(
            body_digest({"a": 1, "b": 2}), body_digest({"b": 2, "a": 1})
        )

    def test_the_digest_is_stable_across_unicode_normalization(self) -> None:
        """The same word typed two ways is the same request.

        A phone keyboard and a desktop one can produce different byte sequences
        for the identical visible string; a retry from the other device must not
        look like a different request.
        """
        composed = "çalış"  # ç as one code point
        decomposed = "çalış"  # c + combining cedilla
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(
            body_digest({"text": composed}), body_digest({"text": decomposed})
        )

    def test_a_different_body_is_a_different_digest(self) -> None:
        self.assertNotEqual(DIGEST_A, DIGEST_B)

    def test_the_digest_does_not_contain_the_content(self) -> None:
        digest = body_digest({"text": "a secret instruction"})
        self.assertNotIn("secret", digest)
        self.assertEqual(len(digest), 64)


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "state" / "idempotency.db"
        self.store = IdempotencyStore(self.path)
        self.addCleanup(self.store.close)


class ClaimTests(StoreTestCase):
    def test_a_first_claim_is_fresh(self) -> None:
        fresh, task_id = self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.assertTrue(fresh)
        self.assertIsNone(task_id)

    def test_a_settled_claim_replays_with_its_task_id(self) -> None:
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-00000001",
            task_id="task_x",
        )
        fresh, task_id = self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.assertFalse(fresh)
        self.assertEqual(task_id, "task_x")

    def test_the_same_id_with_a_different_digest_conflicts(self) -> None:
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-00000001",
            task_id="task_x",
        )
        with self.assertRaises(IdempotencyConflict):
            self.store.claim(
                operation="createTask", scope="project:p",
                request_id="req-00000001", digest=DIGEST_B,
            )

    def test_an_unsettled_claim_reports_in_flight(self) -> None:
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        with self.assertRaises(RequestInFlight):
            self.store.claim(
                operation="createTask", scope="project:p",
                request_id="req-00000001", digest=DIGEST_A,
            )

    def test_a_released_claim_can_be_retried(self) -> None:
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.store.release(
            operation="createTask", scope="project:p", request_id="req-00000001"
        )
        fresh, _ = self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.assertTrue(fresh)

    def test_release_cannot_undo_a_settled_claim(self) -> None:
        """Guarded on ``settled_at IS NULL``, so a misplaced call is harmless."""
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-00000001",
            task_id="task_x",
        )
        self.store.release(
            operation="createTask", scope="project:p", request_id="req-00000001"
        )
        fresh, task_id = self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.assertFalse(fresh)
        self.assertEqual(task_id, "task_x")

    def test_a_stale_claim_is_taken_over(self) -> None:
        """A bridge killed mid-mutation must not brick a request id forever.

        The caller is retrying and cannot choose a different id, so an
        abandoned claim has to become claimable again.
        """
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        # Age the claim past the stale window by writing the column directly —
        # faster and more deterministic than sleeping for two minutes.
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE bridge_requests SET claimed_at = ?",
                (time.time() - STALE_CLAIM_SECONDS - 1,),
            )
        fresh, _ = self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.assertTrue(fresh)

    # -- scoping -------------------------------------------------------------

    def test_the_same_id_is_independent_per_operation(self) -> None:
        for operation in ("createTask", "sendFollowup", "cancelTask", "finishTask"):
            with self.subTest(operation=operation):
                fresh, _ = self.store.claim(
                    operation=operation, scope="task:t", request_id="req-00000001",
                    digest=DIGEST_A,
                )
                self.assertTrue(fresh)

    def test_the_same_id_is_independent_per_scope(self) -> None:
        for scope in ("task:one", "task:two", "project:p"):
            with self.subTest(scope=scope):
                fresh, _ = self.store.claim(
                    operation="sendFollowup", scope=scope,
                    request_id="req-00000001", digest=DIGEST_A,
                )
                self.assertTrue(fresh)

    # -- concurrency and durability ------------------------------------------

    def test_concurrent_identical_claims_produce_exactly_one_winner(self) -> None:
        outcomes = []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def attempt() -> None:
            barrier.wait()
            try:
                fresh, _ = self.store.claim(
                    operation="createTask", scope="project:p",
                    request_id="req-00000001", digest=DIGEST_A,
                )
                with lock:
                    outcomes.append("fresh" if fresh else "replay")
            except RequestInFlight:
                with lock:
                    outcomes.append("in_flight")
            except IdempotencyConflict:  # pragma: no cover - would be a bug
                with lock:
                    outcomes.append("conflict")

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(outcomes.count("fresh"), 1, outcomes)
        self.assertEqual(outcomes.count("in_flight"), 7, outcomes)
        self.assertNotIn("conflict", outcomes)

    def test_a_claim_survives_a_restart(self) -> None:
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-00000001",
            task_id="task_x",
        )
        self.store.close()

        reopened = IdempotencyStore(self.path)
        self.addCleanup(reopened.close)
        fresh, task_id = reopened.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=DIGEST_A,
        )
        self.assertFalse(fresh)
        self.assertEqual(task_id, "task_x")

    def test_no_request_body_is_stored(self) -> None:
        """Only a digest and an id mapping. The table cannot describe a request."""
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-00000001",
            digest=body_digest({"task_text": "a very secret instruction"}),
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-00000001",
            task_id="task_x",
        )
        raw = self.path.read_bytes()
        self.assertNotIn(b"secret instruction", raw)
        # And the schema has no column that could hold one.
        with sqlite3.connect(str(self.path)) as db:
            columns = {
                row[1]
                for row in db.execute("PRAGMA table_info(bridge_requests)")
            }
        self.assertEqual(
            columns,
            {
                "operation",
                "scope",
                "request_id",
                "digest",
                "task_id",
                "claimed_at",
                "settled_at",
            },
        )

    def test_the_store_is_not_the_task_database(self) -> None:
        """One table, and nothing in it is task lifecycle."""
        with sqlite3.connect(str(self.path)) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(tables, {"bridge_requests"})

    def test_old_rows_are_pruned_on_write(self) -> None:
        from cofferdam.actions_bridge.idempotency import RETENTION_SECONDS

        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-old00001",
            digest=DIGEST_A,
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-old00001",
            task_id="task_old",
        )
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "UPDATE bridge_requests SET claimed_at = ?",
                (time.time() - RETENTION_SECONDS - 60,),
            )
        self.store.claim(
            operation="createTask", scope="project:p", request_id="req-new00001",
            digest=DIGEST_A,
        )
        self.store.settle(
            operation="createTask", scope="project:p", request_id="req-new00001",
            task_id="task_new",
        )
        with sqlite3.connect(str(self.path)) as db:
            remaining = {
                row[0] for row in db.execute("SELECT request_id FROM bridge_requests")
            }
        self.assertEqual(remaining, {"req-new00001"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
