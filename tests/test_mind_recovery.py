"""The apply protocol under crashes, and under two callers at once.

The property being defended is narrow and absolute:

    **The store must never durably say `applied` while the canonical Markdown
    still holds the pre-apply bytes.**

An earlier revision committed `applied` and *then* wrote, which bought
exclusivity — two accepts cannot both pass one compare-and-set — at the cost of
a window where exactly that lie was on disk. The protocol now commits an
`applying` claim, writes, and only then records `applied`, so what is durable at
every instant is either an intent or a fact, never a false completion.

That moves the problem rather than removing it: a process can stop while a claim
is outstanding. So the tests below crash it at each boundary that exists and
assert what recovery concludes — and, just as importantly, that recovery **never
writes a canonical document itself**. A consequential operation resumed by a
restart is one nobody authorized at the moment it happened.

Crashes are simulated by failing the real call at the real boundary and then
rebuilding the service from the same database and the same files, which is what
a restart is. Nothing here fakes a state by writing it directly.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import unittest
from contextlib import contextmanager

from ._mind_doubles import MindHarness

ORIGINAL = "# Status\n\noriginal\n"
PROPOSED = "# Status\n\nproposed\n"


class _Stop(Exception):
    """Stands in for the process ending. Not a MindError, so nothing catches it."""


@contextmanager
def crash_at(module, attribute):
    """Make one real syscall raise, to stop the process at exactly that point."""
    original = getattr(module, attribute)

    def stop(*_args, **_kwargs):
        raise _Stop(attribute)

    setattr(module, attribute, stop)
    try:
        yield
    finally:
        setattr(module, attribute, original)


class ApplyProtocol(MindHarness):
    grant_vault = False

    def propose(self):
        self.activate()
        return self.mind.create_proposal(
            scope="project", role="status", content=PROPOSED, reason="y"
        )["proposal_id"]

    def state_of(self, proposal_id):
        return self.mind.get_proposal(proposal_id)["state"]

    def raw_state(self, proposal_id):
        """The durable row, read without going through the service."""
        self.mind_store.close()
        connection = sqlite3.connect(str(self.config.state_dir / "mind" / "mind.sqlite3"))
        try:
            row = connection.execute(
                "SELECT state, applied_hash FROM memory_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        finally:
            connection.close()
        return row

    @contextmanager
    def crash_at_finalize(self):
        """Stop the process between the successful rename and its record.

        Patched on the store instance rather than on a syscall, because that is
        precisely the boundary: the bytes are on disk and the row still says
        `applying`. It is the case that used to be recorded as a completed apply
        and is now reconciled from the file's own hash.
        """
        original = self.mind_store.finalize_applied

        def stop(*_args, **_kwargs):
            raise _Stop("finalize_applied")

        self.mind_store.finalize_applied = stop
        try:
            yield
        finally:
            self.mind_store.finalize_applied = original

    def restart(self):
        """What a service restart is: same database, same files, new objects."""
        self.mind_store.close()
        self.build_services()
        self.activate()
        return self.mind.recover_after_restart()


class NoFalseApplied(ApplyProtocol):
    """The one thing that must never be durably true."""

    def test_a_crash_before_the_write_never_records_applied(self):
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "open"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        state, applied_hash = self.raw_state(proposal_id)
        self.assertNotEqual(state, "applied")
        self.assertIsNone(applied_hash)
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)

    def test_a_crash_after_fsync_but_before_the_rename_never_records_applied(self):
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        state, applied_hash = self.raw_state(proposal_id)
        self.assertNotEqual(state, "applied")
        self.assertIsNone(applied_hash)
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)

    def test_the_claim_is_durable_before_the_filesystem_is_touched(self):
        """`applying` is on disk while the document still holds the old bytes.

        That is the design and not a leak: a claim is a statement of *intent*,
        which is true at the instant it is written and stays true however the
        process ends. `applied` is what must never be premature.
        """
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        state, _ = self.raw_state(proposal_id)
        self.assertEqual(state, "applying")


class RecoveryClassification(ApplyProtocol):
    """The three outcomes, each reached by a real interrupted apply."""

    def test_bytes_landed_is_reconciled_to_applied(self):
        """Case A: the rename succeeded and only the record was lost."""
        proposal_id = self.propose()
        # Stop at the finalize commit — after the rename has already happened.
        # This is the boundary a database error or a power cut between the
        # successful write and its record would land on.
        with self.crash_at_finalize():
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        # The bytes are on disk; the row does not say so yet.
        self.assertEqual(self.project_text("STATUS.md"), PROPOSED)
        self.assertEqual(self.raw_state(proposal_id)[0], "applying")

        tally = self.restart()
        self.assertEqual(tally["applied"], 1)
        stored = self.mind.get_proposal(proposal_id)
        self.assertEqual(stored["state"], "applied")
        self.assertEqual(stored["decided_reason"], "recovered_applied")
        self.assertEqual(stored["applied_hash"], stored["content_hash"])
        # Reconciling metadata performed no write: the bytes were already there.
        self.assertEqual(self.project_text("STATUS.md"), PROPOSED)

    def test_bytes_did_not_land_becomes_interrupted_and_is_not_rewritten(self):
        """Case B: recovery must not finish the job on the user's behalf."""
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        tally = self.restart()
        self.assertEqual(tally["interrupted"], 1)
        stored = self.mind.get_proposal(proposal_id)
        self.assertEqual(stored["state"], "interrupted")
        self.assertEqual(stored["decided_reason"], "interrupted")
        # **The document was not written by recovery.**
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)

    def test_a_third_state_is_conflicted_and_is_not_written(self):
        """Case C: somebody else changed the file while the apply was down."""
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        (self.project_root / "STATUS.md").write_text("someone else\n", encoding="utf-8")
        tally = self.restart()
        self.assertEqual(tally["stale"], 1)
        stored = self.mind.get_proposal(proposal_id)
        self.assertEqual(stored["state"], "stale")
        self.assertEqual(stored["decided_reason"], "recovery_conflicted")
        self.assertEqual(self.project_text("STATUS.md"), "someone else\n")

    def test_an_unresolvable_target_is_interrupted_rather_than_guessed(self):
        """Cofferdam cannot see the file, so it does not claim to know."""
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        (self.project_root / "STATUS.md").unlink()
        self.restart()
        self.assertEqual(self.state_of(proposal_id), "interrupted")

    def test_a_moved_binding_is_interrupted_rather_than_compared(self):
        """Comparing hashes against a different document answers the wrong question."""
        from cofferdam.workstation.mind import documents as documents_module

        (self.project_root / "TWIN.md").write_text(ORIGINAL, encoding="utf-8")
        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        self.write_workspaces(documents={"status": "TWIN.md"})
        self.restart()
        self.assertEqual(self.state_of(proposal_id), "interrupted")
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)
        self.assertEqual(self.project_text("TWIN.md"), ORIGINAL)

    def test_recovery_never_opens_a_file_for_writing(self):
        """Asserted directly rather than inferred from the bytes being unchanged."""
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        self.mind_store.close()
        self.build_services()
        self.activate()

        real_open = documents_module.os.open
        writes = []

        def watched(path, flags, *args, **kwargs):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                writes.append(path)
            return real_open(path, flags, *args, **kwargs)

        documents_module.os.open = watched
        try:
            self.mind.recover_after_restart()
        finally:
            documents_module.os.open = real_open

        self.assertEqual(writes, [])

    def test_recovery_survives_a_host_with_no_active_workspace(self):
        """A project proposal resolves through the workspace service, which
        raises its *own* error type when nothing is active.

        That is an ordinary state for a restarted host, and recovery runs before
        the first request — so a refusal it does not absorb is a daemon that
        will not start. Regression for exactly that.
        """
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        self.mind_store.close()
        self.build_services()
        self.workspaces.deactivate()

        tally = self.mind.recover_after_restart()
        self.assertEqual(tally["interrupted"], 1)
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)

    def test_recovery_survives_a_removed_workspace_configuration(self):
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)

        (self.config.config_dir / "workspaces.json").unlink()
        self.mind_store.close()
        self.build_services()

        tally = self.mind.recover_after_restart()
        self.assertEqual(tally["interrupted"], 1)
        self.assertEqual(self.state_of(proposal_id), "interrupted")

    def test_a_real_application_starts_with_an_outstanding_claim(self):
        """The end-to-end version: `create_app` runs recovery on this database."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - the extras are absent
            self.skipTest("workstation extras are not installed")

        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.service import create_app

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)
        self.mind_store.close()
        self.workspaces.deactivate()
        self.workspace_store.close()

        app = create_app(config=self.config, token="t", adapter=StubAdapter(self.config))
        self.addCleanup(app.state.mind.store.close)
        self.assertEqual(
            app.state.mind.get_proposal(proposal_id)["state"], "interrupted"
        )
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)

    def test_a_restart_with_nothing_outstanding_does_nothing(self):
        self.activate()
        self.assertEqual(
            self.mind.recover_after_restart(),
            {"applied": 0, "interrupted": 0, "stale": 0},
        )

    def test_recovery_creates_no_database_on_an_unconfigured_host(self):
        import shutil

        self.mind_store.close()
        shutil.rmtree(self.config.state_dir / "mind", ignore_errors=True)
        self.build_services()
        self.mind.recover_after_restart()
        self.assertFalse((self.config.state_dir / "mind").exists())


class InterruptedIsDecidableByAPerson(ApplyProtocol):
    """Recovery hands the decision back; it does not make it."""

    def interrupt(self):
        from cofferdam.workstation.mind import documents as documents_module

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)
        self.restart()
        self.assertEqual(self.state_of(proposal_id), "interrupted")
        return proposal_id

    def test_an_explicit_acceptance_completes_it(self):
        proposal_id = self.interrupt()
        self.assertEqual(
            self.mind.accept_proposal(proposal_id)["state"], "applied"
        )
        self.assertEqual(self.project_text("STATUS.md"), PROPOSED)

    def test_an_explicit_rejection_ends_it_without_writing(self):
        proposal_id = self.interrupt()
        self.assertEqual(self.mind.reject_proposal(proposal_id)["state"], "rejected")
        self.assertEqual(self.project_text("STATUS.md"), ORIGINAL)

    def test_it_still_refuses_on_drift(self):
        """Being interrupted does not exempt it from either binding check."""
        from cofferdam.workstation.mind.errors import MindError

        proposal_id = self.interrupt()
        (self.project_root / "STATUS.md").write_text("edited\n", encoding="utf-8")
        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(proposal_id)
        self.assertEqual(caught.exception.code, "mind_proposal_stale")

    def test_a_terminal_proposal_is_never_revived_by_recovery(self):
        proposal_id = self.propose()
        self.mind.reject_proposal(proposal_id)
        self.restart()
        self.assertEqual(self.state_of(proposal_id), "rejected")


class SingleWriter(ApplyProtocol):
    """Two acceptances, one write. The claim is the boundary."""

    def test_a_second_accept_during_an_apply_is_refused(self):
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.mind.errors import MindError

        proposal_id = self.propose()
        started = threading.Event()
        release = threading.Event()
        second: list = []

        real_rename = documents_module.os.rename

        def slow_rename(*args, **kwargs):
            started.set()
            release.wait(5)
            return real_rename(*args, **kwargs)

        def contend():
            started.wait(5)
            try:
                second.append(("ok", self.mind.accept_proposal(proposal_id)["state"]))
            except MindError as rejection:
                second.append(("refused", rejection.code))
            finally:
                release.set()

        documents_module.os.rename = slow_rename
        worker = threading.Thread(target=contend)
        worker.start()
        try:
            first = self.mind.accept_proposal(proposal_id)
        finally:
            release.set()
            worker.join(10)
            documents_module.os.rename = real_rename

        self.assertEqual(first["state"], "applied")
        self.assertEqual(second[0][0], "refused")
        self.assertEqual(second[0][1], "mind_proposal_not_pending")
        self.assertEqual(self.project_text("STATUS.md"), PROPOSED)

    def test_only_one_of_many_concurrent_accepts_applies(self):
        from cofferdam.workstation.mind.errors import MindError

        proposal_id = self.propose()
        outcomes: list = []
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def attempt():
            barrier.wait(5)
            try:
                result = self.mind.accept_proposal(proposal_id)
                with lock:
                    outcomes.append(result["state"])
            except MindError as rejection:
                with lock:
                    outcomes.append(rejection.code)

        workers = [threading.Thread(target=attempt) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)

        self.assertEqual(outcomes.count("applied"), 1, outcomes)
        self.assertEqual(len(outcomes), 4)
        self.assertEqual(self.project_text("STATUS.md"), PROPOSED)

    def test_unrelated_files_are_untouched_throughout(self):
        before = {
            name: (self.project_root / name).read_bytes()
            for name in ("ROADMAP.md", "UNRELATED.md")
        }
        proposal_id = self.propose()
        self.mind.accept_proposal(proposal_id)
        for name, content in before.items():
            self.assertEqual((self.project_root / name).read_bytes(), content, name)


class ReplayAfterRecovery(ApplyProtocol):
    def test_a_recovered_applied_proposal_cannot_be_replayed(self):
        from cofferdam.workstation.mind.errors import MindError

        proposal_id = self.propose()
        with self.crash_at_finalize():
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)
        self.restart()
        self.assertEqual(self.state_of(proposal_id), "applied")

        (self.project_root / "STATUS.md").write_text("later work\n", encoding="utf-8")
        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(proposal_id)
        self.assertEqual(caught.exception.code, "mind_proposal_not_pending")
        self.assertEqual(self.project_text("STATUS.md"), "later work\n")

    def test_a_conflicted_proposal_cannot_be_replayed(self):
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.mind.errors import MindError

        proposal_id = self.propose()
        with crash_at(documents_module.os, "rename"):
            with self.assertRaises(_Stop):
                self.mind.accept_proposal(proposal_id)
        (self.project_root / "STATUS.md").write_text("someone else\n", encoding="utf-8")
        self.restart()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(proposal_id)
        self.assertEqual(caught.exception.code, "mind_proposal_not_pending")
        self.assertEqual(self.project_text("STATUS.md"), "someone else\n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
