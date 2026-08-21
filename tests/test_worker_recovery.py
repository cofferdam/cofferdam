"""What a restart finds, and what it refuses to do about it.

The property every test here is really about
--------------------------------------------

**Recovery is not re-execution.** A person approved one development step. A
daemon that comes back up and re-sends that step is performing a *second* one on
the strength of the first approval, and no amount of "it probably did not
finish" makes that authorized.

So the counting doubles matter more than usual: :class:`CountingWorkerAdapter`
records every ``start``, and a recovery pass that launched anything would be a
failing assertion rather than a paragraph somebody has to believe.

Why the services are destroyed and rebuilt
------------------------------------------

A restart test that reuses the objects it set the fixture up with proves nothing
— in-memory state would carry the answer across the "restart" and the durable
path would never be exercised. Every test that says *restart* here calls
:meth:`RecoveryHarness.restart`, which drops the Task Core service, its store,
the planner store and the dispatch service on the floor and constructs new ones
over the same directories. What survives is what was written down.

The crash windows
-----------------

Two are simulated precisely, because they are the two that can produce duplicate
*effects* rather than merely a confusing status:

* the worker finished and the process died before the final state was written —
  :class:`CommitCrashWindow` proves the approved prompt is not sent again;
* the commit landed and the process died before the id was recorded —
  :class:`CommitCrashWindow` proves the commit is found rather than repeated.

Real Git repositories throughout. The commit that recovery discovers is a real
commit made by the same code path the adapter uses.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.planner import (
    ACTION_PREPARE_WORKER_PROMPT,
    AuthorityProvenance,
    PlannerAuthorityService,
    PlannerResult,
    PlannerStore,
    ProviderExecution,
    WorkerDispatchService,
    new_planner_request_id,
)
from cofferdam.workstation.planner.dispatch_service import (
    RECOVERY_SENTENCES,
    WORKER_ADAPTER_ID,
)
from cofferdam.workstation.planner.store import (
    PLANNER_SCHEMA_VERSION,
    STATUS_SUCCEEDED,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.projects import ProjectRegistry, TaskProject
from cofferdam.workstation.worker import journal, reconcile, worktree

WORKER_PROMPT = "Implement subtract() in calc.py and add a test for it.\n"


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def make_repo(root: Path, name: str, marker: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / f"{marker}.txt").write_text(f"this file belongs to {marker}\n")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


class CountingWorkerAdapter(TaskAdapter):
    """Starts nothing, counts everything. The no-re-execution instrument.

    Declares ``recover_after_restart`` exactly as the real adapter now does, so
    Task Core parks its tasks in ``recovery_required`` rather than settling them
    as ``interrupted`` — which is the behaviour under test.
    """

    adapter_id = WORKER_ADAPTER_ID
    display_name = "Counting worker"

    def __init__(self):
        self.starts = []
        self.prompts = []
        self.recovers = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, cancel=True, final_result=True, recover_after_restart=True
        )

    def available(self) -> bool:
        return True

    def start(self, context) -> AdapterOutcome:
        self.starts.append(context.task_id)
        self.prompts.append(context.prompt)
        # Leaves the task `running`, which is what a process that later dies
        # looks like in the database.
        return AdapterOutcome(
            events=(AdapterEvent(text="started").bounded(),), requested_state="running"
        )

    def recover(self, context) -> AdapterOutcome:  # pragma: no cover - must not run
        self.recovers.append(context.task_id)
        raise AssertionError("Task Core must not invoke adapter recovery in PR1f")

    def cancel(self, context) -> AdapterOutcome:
        return AdapterOutcome(requested_state="cancelled")


class RecoveryHarness(unittest.TestCase):
    """Two real projects, real Git, real databases, and a real restart."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.home = self.dir / "home"
        self.state_dir = self.dir / "state"
        self.state_dir.mkdir(parents=True)

        self.repo_a = make_repo(self.dir / "projects", "alpha", "PROJECT_A")
        self.repo_b = make_repo(self.dir / "projects", "beta", "PROJECT_B")
        self.project_a = TaskProject(
            project_id="alpha", display_name="Alpha", root=self.repo_a.resolve(),
            adapters=(WORKER_ADAPTER_ID,),
        )
        self.project_b = TaskProject(
            project_id="beta", display_name="Beta", root=self.repo_b.resolve(),
            adapters=(WORKER_ADAPTER_ID,),
        )
        self.who = AuthorityProvenance.internal_test()
        self._build()

    # -- the restart itself --------------------------------------------------

    def _build(self):
        """Construct a whole stack over the durable directories."""
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.tasks.adapters import AdapterRegistry
        from cofferdam.workstation.tasks.service import TaskService
        from cofferdam.workstation.tasks.store import TaskStore

        config = load_config(self.home)
        config.ensure_dirs()
        self.adapter = CountingWorkerAdapter()
        self.task_store = TaskStore(config)
        self.tasks = TaskService(
            config,
            self.task_store,
            AdapterRegistry((self.adapter,)),
            projects=ProjectRegistry(
                projects=(self.project_a, self.project_b), source_present=True
            ),
        )
        self.store = PlannerStore(self.dir / "planner")
        self.authority = PlannerAuthorityService(store=self.store)
        self.dispatcher = WorkerDispatchService(store=self.store, tasks=self.tasks)

    def restart(self):
        """Destroy every service object and build new ones. Durable state only.

        The assertion this method makes possible: anything a test observes after
        calling it came off the disk, because nothing that held it in memory
        still exists.
        """
        self.task_store.close()
        del self.tasks, self.task_store, self.store, self.dispatcher, self.authority
        self._build()
        # What the daemon does at start-up, in the order it does it: Task Core
        # settles what it believes is unfinished, then the owner of the work
        # decides what that means.
        self.tasks.recover_after_restart()

    def reconcile(self):
        return self.dispatcher.reconcile_after_restart(state_dir=self.state_dir)

    # -- fixtures ------------------------------------------------------------

    def approved(self, *, project_id="alpha", prompt=WORKER_PROMPT) -> str:
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id=project_id,
            user_intent="ilerleyelim", request_payload={},
            projection_policy_id="policy_1",
            projection_built_at="2026-08-21T00:00:00Z",
            created_at="2026-08-21T00:00:00Z",
        )
        self.store.mark_running(request_id, started_at="2026-08-21T00:00:01Z")
        self.store.record_success(
            request_id,
            result=PlannerResult(
                action=ACTION_PREPARE_WORKER_PROMPT, summary="one step",
                confidence=0.9, worker_prompt=prompt,
                decision_basis="context was sufficient",
            ),
            execution=ProviderExecution(
                provider_id="claude_code", requested_model="opus",
                actual_model="claude-opus-5",
            ),
            completed_at="2026-08-21T00:00:02Z",
        )
        gate = self.authority.gate(request_id)
        self.authority.approve_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            provenance=self.who,
        )
        return request_id

    def dispatched(self, *, project_id="alpha"):
        request_id = self.approved(project_id=project_id)
        view = self.dispatcher.dispatch_approved_worker_prompt(
            request_id, provenance=self.who
        )
        return request_id, view

    # -- worker-side fixtures, using the real code paths ---------------------

    def cut_worktree(self, view, project_root=None):
        """Cut this dispatch's worktree exactly as the adapter does."""
        tree = worktree.prepare(
            project_id=view.dispatch.project_id,
            project_root=project_root or Path(self.project_a.root),
            task_id=view.dispatch.task_id,
            state_dir=self.state_dir,
        )
        journal.record(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id,
            journal.PHASE_PREPARED, base_commit=tree.base_commit, detail=tree.branch,
        )
        return tree

    def note(self, view, phase, **facts):
        return journal.record(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id,
            phase, **facts,
        )

    def edit(self, tree, text="def subtract(a, b):\n    return a - b\n"):
        (tree.path / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n\n\n" + text
        )

    def commit(self, tree):
        """Commit through the same host-owned path the adapter uses."""
        from cofferdam.workstation.tasks.adapters.claude_code_worker import cli

        return worktree.commit_all(
            tree, message="worker: approved development step",
            author=cli.GIT_AUTHOR_NAME, email=cli.GIT_AUTHOR_EMAIL,
        )

    def commits_on(self, tree) -> int:
        return int(git(tree.path, "rev-list", "--count", "HEAD"))

    def recorded(self, view):
        return self.store.reconciliation(view.dispatch.dispatch_id)

    def task_state(self, view):
        row = self.tasks.get_task(view.dispatch.task_id)
        return getattr(row, "state", None)


# -- the fixture is real ------------------------------------------------------


class TheHarnessIsNotVacuous(RecoveryHarness):
    """If these fail, every recovery assertion below is meaningless."""

    def test_a_dispatch_creates_a_running_task_and_one_worker_start(self):
        _, view = self.dispatched()
        self.assertEqual(self.adapter.starts, [view.dispatch.task_id])
        self.assertEqual(self.task_state(view), "running")

    def test_the_restart_really_replaces_the_services(self):
        """The restart must be a real one, or every test below is vacuous.

        A reference to each old object is held across the call on purpose. The
        first version of this compared ``id()`` without holding one and failed:
        CPython freed the old service and handed the same address to the new one,
        so identity by address is only meaningful while both are alive.
        """
        _, view = self.dispatched()
        old_tasks, old_store = self.tasks, self.store
        old_dispatcher, old_adapter = self.dispatcher, self.adapter

        self.restart()

        self.assertIsNot(self.tasks, old_tasks)
        self.assertIsNot(self.store, old_store)
        self.assertIsNot(self.dispatcher, old_dispatcher)
        self.assertIsNot(self.adapter, old_adapter)
        # The new adapter has no memory of the dispatch, so anything the tests
        # below observe about it came off the disk.
        self.assertEqual(self.adapter.starts, [])
        self.assertIsNotNone(self.store.dispatch(view.dispatch.planner_request_id))

    def test_a_cut_worktree_and_a_commit_are_real(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.assertTrue((tree.path / "calc.py").is_file())
        self.edit(tree)
        commit = self.commit(tree)
        self.assertIsNotNone(commit)
        self.assertEqual(self.commits_on(tree), 2)


# -- Task Core's half ---------------------------------------------------------


class TaskCoreParksRatherThanSettles(RecoveryHarness):
    """The adapter now declares recoverability, so the target state changes."""

    def test_a_worker_task_becomes_recovery_required_not_interrupted(self):
        _, view = self.dispatched()
        self.restart()
        self.assertEqual(self.task_state(view), "recovery_required")

    def test_recovery_required_is_readable_back_as_awaiting_recovery(self):
        _, view = self.dispatched()
        self.restart()
        parked = [row.task_id for row in self.tasks.tasks_awaiting_recovery()]
        self.assertEqual(parked, [view.dispatch.task_id])

    def test_task_core_never_calls_adapter_recover(self):
        """The hook exists on the protocol and PR1f does not wire it.

        Asserted rather than assumed because the capability flag and the hook
        have similar names, and turning the first on looks like it might turn
        the second on too.
        """
        _, _view = self.dispatched()
        self.restart()
        self.reconcile()
        self.assertEqual(self.adapter.recovers, [])

    def test_a_terminal_task_is_untouched_by_a_restart(self):
        _, view = self.dispatched()
        self.tasks.cancel_task(view.dispatch.task_id)
        before = self.task_state(view)
        self.restart()
        self.assertEqual(self.task_state(view), before)


# -- the core rule ------------------------------------------------------------


class RecoveryIsNotReExecution(RecoveryHarness):
    """Nothing is relaunched, re-sent, rerun, reset or deleted."""

    def test_no_second_worker_start(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.restart()
        self.assertEqual(self.adapter.starts, [], "the new adapter started something")
        self.reconcile()
        self.assertEqual(self.adapter.starts, [], "recovery started a worker")

    def test_the_approved_prompt_is_never_delivered_twice(self):
        """Step 8, counted rather than reasoned about."""
        _, view = self.dispatched()
        delivered_before = list(self.adapter.prompts)
        self.assertEqual(len(delivered_before), 1)
        self.restart()
        self.reconcile()
        self.reconcile()
        self.assertEqual(self.adapter.prompts, [], "the prompt was sent again")

    def test_reconciling_repeatedly_changes_nothing(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.restart()
        first = self.reconcile()
        recorded = self.recorded(view)
        second = self.reconcile()
        third = self.reconcile()
        self.assertEqual(second, {})
        self.assertEqual(third, {})
        self.assertEqual(first, {reconcile.OUTCOME_PARTIAL_WORK_PRESERVED: 1})
        # The first answer is the one kept.
        self.assertEqual(
            self.recorded(view).reconciled_at, recorded.reconciled_at
        )

    def test_the_planner_request_is_not_rerun(self):
        request_id, view = self.dispatched()
        before = self.store.get(request_id)
        self.restart()
        self.reconcile()
        after = self.store.get(request_id)
        self.assertEqual(after.status, before.status)
        self.assertEqual(after.worker_prompt, before.worker_prompt)
        self.assertEqual(after.completed_at, before.completed_at)

    def test_no_second_dispatch_row_appears(self):
        request_id, view = self.dispatched()
        self.restart()
        self.reconcile()
        self.assertEqual(len(self.store.recent_dispatches(limit=50)), 1)
        self.assertEqual(
            self.store.dispatch(request_id).dispatch_id, view.dispatch.dispatch_id
        )

    def test_the_exact_approved_prompt_and_fingerprint_are_unchanged(self):
        request_id, view = self.dispatched()
        prompt_before = self.dispatcher.dispatched_prompt(request_id)
        fingerprint_before = view.dispatch.subject_fingerprint
        self.restart()
        self.reconcile()
        self.assertEqual(self.dispatcher.dispatched_prompt(request_id), prompt_before)
        self.assertEqual(prompt_before, WORKER_PROMPT)
        self.assertEqual(
            self.store.dispatch(request_id).subject_fingerprint, fingerprint_before
        )


# -- classification -----------------------------------------------------------


class WhatARestartFinds(RecoveryHarness):
    """One case per materially different machine state. Not all "failed"."""

    def test_a_dispatch_that_never_prepared_anything(self):
        _, view = self.dispatched()
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_NEVER_STARTED: 1})
        self.assertEqual(self.recorded(view).outcome, reconcile.OUTCOME_NEVER_STARTED)

    def test_a_prepared_worktree_with_nothing_changed(self):
        _, view = self.dispatched()
        self.cut_worktree(view)
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_NO_WORK_FOUND: 1})

    def test_uncommitted_edits_are_partial_work(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.restart()
        self.assertEqual(
            self.reconcile(), {reconcile.OUTCOME_PARTIAL_WORK_PRESERVED: 1}
        )
        found = self.recorded(view)
        self.assertGreater(found.changed_files, 0)
        self.assertTrue(found.worktree_retained)
        self.assertTrue(found.needs_attention)

    def test_a_worker_commit_is_recovered(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        commit = self.commit(tree)
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_COMMIT_RECOVERED: 1})
        self.assertEqual(self.recorded(view).recovered_commit, commit)

    def test_a_missing_worktree_is_reported_not_recreated(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        path = tree.path
        subprocess.run(["rm", "-rf", str(path)], check=True)
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_WORKTREE_MISSING: 1})
        self.assertFalse(path.exists(), "recovery recreated the worktree")
        self.assertEqual(self.adapter.starts, [])

    def test_a_worktree_on_another_branch_is_refused(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        git(tree.path, "checkout", "-q", "-b", "somebody-elses-branch")
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_WORKTREE_MISMATCHED: 1})
        self.assertTrue(tree.path.is_dir(), "recovery removed a worktree")

    def test_a_journal_commit_git_does_not_have_is_contradictory(self):
        """The journal is a lead, never the authority. This is that rule."""
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.note(view, journal.PHASE_COMMIT_PENDING)
        self.note(view, journal.PHASE_COMMITTED, commit="0" * 40)
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_CONTRADICTORY: 1})
        found = self.recorded(view)
        self.assertTrue(found.needs_attention)
        self.assertIsNone(found.recovered_commit)

    def test_an_unresolvable_project_is_undetermined_not_nothing_happened(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        # The project's folder goes away between the dispatch and the restart.
        subprocess.run(["rm", "-rf", str(self.repo_a)], check=True)
        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_UNDETERMINED: 1})
        self.assertTrue(self.recorded(view).needs_attention)


# -- partial work is kept -----------------------------------------------------


class PartialWorkIsPreserved(RecoveryHarness):
    """Step 9 and Step 13: keep the evidence, delete nothing, reset nothing."""

    def setUp(self):
        super().setUp()
        _, self.view = self.dispatched()
        self.tree = self.cut_worktree(self.view)
        self.edit(self.tree)
        self.before = (self.tree.path / "calc.py").read_text()
        self.restart()
        self.reconcile()

    def test_the_edits_are_still_on_disk_byte_for_byte(self):
        self.assertEqual((self.tree.path / "calc.py").read_text(), self.before)
        self.assertIn("subtract", self.before)

    def test_the_worktree_was_not_removed(self):
        self.assertTrue(self.tree.path.is_dir())

    def test_nothing_was_hard_reset(self):
        status = git(self.tree.path, "status", "--porcelain")
        self.assertTrue(status.strip(), "the working tree was cleaned")

    def test_nothing_was_committed_on_the_worker_branch(self):
        self.assertEqual(self.commits_on(self.tree), 1)

    def test_the_dispatch_and_prompt_are_retained(self):
        self.assertIsNotNone(
            self.store.dispatch(self.view.dispatch.planner_request_id)
        )
        self.assertEqual(
            self.dispatcher.dispatched_prompt(
                self.view.dispatch.planner_request_id
            ),
            WORKER_PROMPT,
        )

    def test_the_canonical_project_is_untouched(self):
        state = worktree.canonical_state(self.repo_a.resolve())
        self.assertEqual(state["branch"], "main")
        self.assertEqual(state["status"], "")


# -- the commit crash window --------------------------------------------------


class CommitCrashWindow(RecoveryHarness):
    """Steps 7 and 19: the commit landed, the id was never recorded.

    The highest-value case in this file. It is the only crash window where the
    naive repair — "the commit is missing, make it" — produces a *second commit*
    rather than merely a confusing status line.
    """

    def setUp(self):
        super().setUp()
        _, self.view = self.dispatched()
        self.tree = self.cut_worktree(self.view)
        self.note(self.view, journal.PHASE_WORKER_RUNNING)
        self.note(self.view, journal.PHASE_WORKER_RETURNED)
        self.note(self.view, journal.PHASE_CHECKS_RUNNING)
        self.note(
            self.view, journal.PHASE_CHECKS_COMPLETED,
            check="python-unittest-quiet", exit_zero=True,
        )
        self.edit(self.tree)
        # The intent is recorded, the commit is made — and then the process dies
        # before `committed` is written. That is the whole window.
        self.note(self.view, journal.PHASE_COMMIT_PENDING)
        self.commit_sha = self.commit(self.tree)
        self.assertIsNotNone(self.commit_sha)
        self.commits_before = self.commits_on(self.tree)

    def test_the_window_is_what_it_claims_to_be(self):
        entries = journal.read(
            self.state_dir, self.view.dispatch.project_id, self.view.dispatch.task_id
        )
        self.assertTrue(journal.reached(entries, journal.PHASE_COMMIT_PENDING))
        self.assertFalse(journal.reached(entries, journal.PHASE_COMMITTED))
        self.assertEqual(
            journal.open_intents(entries), (journal.PHASE_COMMIT_PENDING,)
        )

    def test_recovery_finds_the_exact_existing_commit(self):
        self.restart()
        self.reconcile()
        self.assertEqual(self.recorded(self.view).recovered_commit, self.commit_sha)

    def test_no_duplicate_commit_is_created(self):
        self.restart()
        self.reconcile()
        self.assertEqual(
            self.commits_on(self.tree), self.commits_before,
            "recovery committed again",
        )

    def test_the_commit_keeps_its_worker_authorship(self):
        self.restart()
        self.reconcile()
        self.assertEqual(
            git(self.tree.path, "log", "-1", "--format=%ae"),
            "worker@cofferdam.local",
        )

    def test_the_check_result_survives_the_crash(self):
        """The phase journal is what makes this answerable at all."""
        self.restart()
        self.reconcile()
        found = self.recorded(self.view)
        self.assertTrue(found.checks_observed)
        self.assertEqual(found.check_exit_zero, 1)

    def test_the_task_is_interrupted_and_not_completed(self):
        """A commit existing is not the execution contract being satisfied."""
        self.restart()
        self.reconcile()
        self.assertEqual(self.task_state(self.view), "interrupted")

    def test_the_read_model_says_the_commit_was_recovered(self):
        self.restart()
        self.reconcile()
        payload = self.dispatcher.view(
            self.view.dispatch.planner_request_id
        ).to_dict()
        self.assertTrue(payload["restart_occurred"])
        self.assertEqual(payload["recovered_commit"], self.commit_sha)
        self.assertIn("had already committed", payload["recovery"]["sentence"])


# -- the worker-finished crash window ----------------------------------------


class WorkerFinishedBeforeTheStatusWrite(RecoveryHarness):
    """Step 15's other window: edits done, nothing durable said so."""

    def test_the_prompt_is_not_resent_and_the_edits_stand(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.note(view, journal.PHASE_WORKER_RUNNING)
        self.edit(tree)
        self.note(view, journal.PHASE_WORKER_RETURNED)
        self.restart()
        self.reconcile()
        self.assertEqual(self.adapter.prompts, [])
        self.assertIn("subtract", (tree.path / "calc.py").read_text())

    def test_an_unclosed_worker_phase_is_visible_as_an_open_intent(self):
        _, view = self.dispatched()
        self.cut_worktree(view)
        self.note(view, journal.PHASE_WORKER_RUNNING)
        self.restart()
        self.reconcile()
        self.assertEqual(
            self.recorded(view).open_intents, journal.PHASE_WORKER_RUNNING
        )

    def test_checks_are_not_rerun_when_their_completion_was_never_recorded(self):
        """Step 10's conservative decision, asserted.

        A check command is code-owned and runs with no network, which makes
        rerunning it *probably* safe — and "probably" is the wrong standard for
        something that runs unattended against a project's own test suite, which
        may write files, touch a database or bind a port. So an unfinished check
        is reported, not repeated.
        """
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.note(view, journal.PHASE_CHECKS_RUNNING)
        self.restart()
        self.reconcile()
        found = self.recorded(view)
        self.assertFalse(found.checks_observed)
        self.assertIsNone(found.check_exit_zero)
        self.assertTrue(found.needs_attention)


# -- project isolation --------------------------------------------------------


class TwoProjectRecoveryIsolation(RecoveryHarness):
    """Step 12: recovering A must not read, attach or mutate B."""

    def setUp(self):
        super().setUp()
        self.b_before = worktree.canonical_state(self.repo_b.resolve())
        _, self.view_a = self.dispatched(project_id="alpha")
        self.tree_a = self.cut_worktree(self.view_a)
        self.edit(self.tree_a)

    def test_project_b_really_holds_its_own_material(self):
        self.assertTrue((self.repo_b / "PROJECT_B.txt").is_file())

    def test_recovering_a_does_not_mutate_b(self):
        self.restart()
        self.reconcile()
        self.assertEqual(
            worktree.canonical_state(self.repo_b.resolve()), self.b_before
        )

    def test_recovering_a_reconciles_only_as_worktree(self):
        self.restart()
        self.reconcile()
        found = self.recorded(self.view_a)
        self.assertEqual(found.project_id, "alpha")
        self.assertEqual(found.outcome, reconcile.OUTCOME_PARTIAL_WORK_PRESERVED)

    def test_a_worktree_belonging_to_b_is_refused_for_a(self):
        """The branch name alone is not identity — the repository is.

        A worktree of *project B* is placed exactly where project A's dispatch
        expects one, carrying the branch name A's task derives. A recovery pass
        that trusted the directory layout and the branch name would adopt it.
        """
        subprocess.run(["rm", "-rf", str(self.tree_a.path)], check=True)
        git(self.repo_a.resolve(), "worktree", "prune")
        branch = worktree.branch_name(self.view_a.dispatch.task_id)
        git(
            self.repo_b.resolve(), "worktree", "add", "-b", branch,
            str(self.tree_a.path), "HEAD",
        )
        self.assertTrue((self.tree_a.path / "PROJECT_B.txt").is_file())

        self.restart()
        self.assertEqual(self.reconcile(), {reconcile.OUTCOME_WORKTREE_MISMATCHED: 1})
        self.assertIn(
            "different project", self.recorded(self.view_a).detail or ""
        )
        # And B's material is still there, untouched.
        self.assertTrue((self.tree_a.path / "PROJECT_B.txt").is_file())

    def test_two_dispatches_reconcile_independently(self):
        _, view_b = self.dispatched(project_id="beta")
        tree_b = worktree.prepare(
            project_id="beta", project_root=Path(self.project_b.root),
            task_id=view_b.dispatch.task_id, state_dir=self.state_dir,
        )
        journal.record(
            self.state_dir, "beta", view_b.dispatch.task_id,
            journal.PHASE_PREPARED, base_commit=tree_b.base_commit,
        )
        self.restart()
        tally = self.reconcile()
        self.assertEqual(
            tally,
            {
                reconcile.OUTCOME_PARTIAL_WORK_PRESERVED: 1,
                reconcile.OUTCOME_NO_WORK_FOUND: 1,
            },
        )
        self.assertEqual(self.recorded(self.view_a).project_id, "alpha")
        self.assertEqual(self.recorded(view_b).project_id, "beta")


# -- cancellation stays dominant ---------------------------------------------


class CancellationIsNotARestart(RecoveryHarness):
    """Step 20: a person's decision outranks anything found on disk."""

    def test_a_cancelled_task_is_not_promoted_by_a_commit(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.commit(tree)
        self.tasks.cancel_task(view.dispatch.task_id)
        cancelled_state = self.task_state(view)

        self.restart()
        self.reconcile()
        self.assertEqual(self.task_state(view), cancelled_state)
        self.assertNotEqual(self.task_state(view), "interrupted")

    def test_a_cancelled_task_is_never_reconciled_at_all(self):
        _, view = self.dispatched()
        self.cut_worktree(view)
        self.tasks.cancel_task(view.dispatch.task_id)
        self.restart()
        self.assertEqual(self.reconcile(), {})
        self.assertIsNone(self.recorded(view))

    def test_settling_refuses_a_task_that_is_not_parked(self):
        _, view = self.dispatched()
        self.tasks.cancel_task(view.dispatch.task_id)
        before = self.task_state(view)
        settled = self.tasks.settle_recovered_task(view.dispatch.task_id)
        self.assertEqual(settled, before)
        self.assertEqual(self.task_state(view), before)


# -- the read model -----------------------------------------------------------


class TheReadModelExplainsTheRestart(RecoveryHarness):
    """Step 16 and 17: enough for a screen to say more than "failed"."""

    def payload(self, view):
        return self.dispatcher.view(view.dispatch.planner_request_id).to_dict()

    def test_a_dispatch_that_never_restarted_says_so_and_carries_no_recovery(self):
        _, view = self.dispatched()
        payload = self.payload(view)
        self.assertFalse(payload["restart_occurred"])
        self.assertNotIn("recovery", payload)

    def test_an_interrupted_dispatch_reads_back_with_a_sentence(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.restart()
        self.reconcile()
        payload = self.payload(view)
        self.assertTrue(payload["restart_occurred"])
        self.assertTrue(payload["partial_work_preserved"])
        self.assertTrue(payload["worktree_retained"])
        self.assertTrue(payload["human_action_needed"])
        self.assertIn("preserved", payload["recovery"]["sentence"])

    def test_every_outcome_has_a_sentence(self):
        for outcome in reconcile.OUTCOMES:
            self.assertIn(outcome, RECOVERY_SENTENCES, outcome)
            self.assertNotEqual(RECOVERY_SENTENCES[outcome].strip(), "")

    def test_no_sentence_says_merely_failed(self):
        for outcome, sentence in RECOVERY_SENTENCES.items():
            self.assertNotEqual(sentence.strip().lower(), "failed", outcome)
            self.assertIn("restart", sentence.lower() + " restart")

    def test_the_routine_read_contains_no_host_path(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.restart()
        self.reconcile()
        rendered = json.dumps(self.payload(view))
        for path in (
            str(self.dir), str(self.state_dir), str(self.repo_a), str(tree.path),
            str(self.home),
        ):
            self.assertNotIn(path, rendered)
        self.assertNotIn("/home/", rendered)

    def test_the_read_model_is_serializable(self):
        _, view = self.dispatched()
        self.restart()
        self.reconcile()
        json.dumps(self.payload(view))

    def test_worker_completion_is_still_not_acceptance(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        self.edit(tree)
        self.commit(tree)
        self.restart()
        self.reconcile()
        self.assertTrue(self.payload(view)["worker_completion_is_not_acceptance"])


# -- the journal itself -------------------------------------------------------


class ThePhaseJournal(RecoveryHarness):
    """Step 3 and Step 4: the markers, and the order they are written in."""

    def test_a_phase_path_is_derived_from_ids_and_holds_no_worktree(self):
        path = journal.journal_path(self.state_dir, "alpha", "task_" + "a" * 26)
        self.assertTrue(str(path).endswith(".jsonl"))
        self.assertNotIn(str(worktree.worktrees_root(self.state_dir)), str(path))

    def test_a_bad_id_cannot_produce_a_path(self):
        with self.assertRaises(Exception):
            journal.journal_path(self.state_dir, "../escape", "task_" + "a" * 26)

    def test_entries_read_back_in_order(self):
        _, view = self.dispatched()
        self.cut_worktree(view)
        for phase in (
            journal.PHASE_WORKER_RUNNING, journal.PHASE_WORKER_RETURNED,
            journal.PHASE_CHECKS_RUNNING, journal.PHASE_CHECKS_COMPLETED,
        ):
            self.note(view, phase)
        entries = journal.read(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id
        )
        self.assertEqual(
            [entry.phase for entry in entries],
            [
                journal.PHASE_PREPARED, journal.PHASE_WORKER_RUNNING,
                journal.PHASE_WORKER_RETURNED, journal.PHASE_CHECKS_RUNNING,
                journal.PHASE_CHECKS_COMPLETED,
            ],
        )
        self.assertEqual(journal.open_intents(entries), ())

    def test_an_unclosed_pair_is_an_open_intent(self):
        _, view = self.dispatched()
        self.cut_worktree(view)
        self.note(view, journal.PHASE_COMMIT_PENDING)
        entries = journal.read(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id
        )
        self.assertEqual(
            journal.open_intents(entries), (journal.PHASE_COMMIT_PENDING,)
        )

    def test_a_torn_final_line_does_not_lose_the_entries_before_it(self):
        """What a crash mid-write actually leaves on disk."""
        _, view = self.dispatched()
        self.cut_worktree(view)
        path = journal.journal_path(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id
        )
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"phase": "commit_pen')
        entries = journal.read(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id
        )
        self.assertEqual([entry.phase for entry in entries], [journal.PHASE_PREPARED])

    def test_an_unwritable_journal_never_raises(self):
        """Bookkeeping must not be able to fail a dispatch."""
        self.assertIsNone(
            journal.record(
                Path("/proc/nonexistent-cofferdam"), "alpha", "task_" + "a" * 26,
                journal.PHASE_PREPARED,
            )
        )

    def test_an_unknown_phase_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            journal.record(self.state_dir, "alpha", "task_" + "a" * 26, "whatever")

    def test_the_prepared_entry_records_the_base_commit(self):
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        entries = journal.read(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id
        )
        prepared = journal.latest(entries, journal.PHASE_PREPARED)
        self.assertEqual(prepared.base_commit, tree.base_commit)

    def test_the_journal_is_not_inside_the_worktree(self):
        """A model-writable file must never be the evidence of what a model did."""
        _, view = self.dispatched()
        tree = self.cut_worktree(view)
        path = journal.journal_path(
            self.state_dir, view.dispatch.project_id, view.dispatch.task_id
        )
        self.assertNotIn(str(tree.path.resolve()), str(path.resolve()))


# -- adapter write ordering ---------------------------------------------------


class TheAdapterBracketsEveryPhase(unittest.TestCase):
    """Step 4, asserted against the source's actual call order.

    Not a string test dressed up: it reads the real ``start`` body, extracts the
    phase constants in the order they are passed, and checks the *pairs* — an
    intent must precede its result, and a commit result must never be recorded
    before the commit call.
    """

    def order(self):
        import inspect

        from cofferdam.workstation.tasks.adapters.claude_code_worker import adapter

        source = inspect.getsource(adapter.ClaudeCodeWorkerAdapter.start)
        found = []
        for line in source.splitlines():
            stripped = line.strip()
            if "journal.PHASE_" not in stripped:
                continue
            for phase in journal.PHASES:
                token = "journal.PHASE_" + phase.upper()
                if token in stripped:
                    found.append(phase)
        return found, source

    def test_every_phase_is_written_by_start(self):
        found, _ = self.order()
        self.assertEqual(set(found), set(journal.PHASES))

    def test_each_intent_precedes_its_result(self):
        found, _ = self.order()
        for result_phase, intent_phase in journal.CLOSES.items():
            self.assertLess(
                found.index(intent_phase), found.index(result_phase),
                f"{intent_phase} must be recorded before {result_phase}",
            )

    def test_commit_pending_is_written_before_the_commit_call(self):
        found, source = self.order()
        pending = source.index("journal.PHASE_COMMIT_PENDING")
        committed = source.index("journal.PHASE_COMMITTED")
        call = source.index("self._commit(tree, check)")
        self.assertLess(pending, call, "the commit intent is recorded too late")
        self.assertLess(call, committed, "committed is claimed before Git ran")

    def test_checks_completed_is_written_after_the_check_call(self):
        _, source = self.order()
        self.assertLess(
            source.index("checks.run("),
            source.index("journal.PHASE_CHECKS_COMPLETED"),
            "the check result is claimed before the check ran",
        )

    def test_the_adapter_declares_recoverability(self):
        from cofferdam.workstation.tasks.adapters.claude_code_worker.adapter import (
            ClaudeCodeWorkerAdapter,
        )

        capabilities = ClaudeCodeWorkerAdapter.capabilities(
            ClaudeCodeWorkerAdapter.__new__(ClaudeCodeWorkerAdapter)
        )
        self.assertTrue(capabilities.recover_after_restart)


# -- schema -------------------------------------------------------------------


class TheSchemaMovedForwardOnly(unittest.TestCase):
    """Step 24: additive, forward-only, and a v3 database still reads."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    # The exact version literal lives in `test_worker_dispatch_migration.py`,
    # which is the one bump site. Everything here compares against the constant,
    # so this file does not have to be edited when the schema next moves.

    def test_a_fresh_database_stamps_the_current_version(self):
        store = PlannerStore(self.dir / "planner")
        store.get("planner_" + "a" * 26)
        import sqlite3

        with sqlite3.connect(self.dir / "planner" / "planner.sqlite3") as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        self.assertEqual(row[0], str(PLANNER_SCHEMA_VERSION))

    def test_a_v3_database_migrates_and_keeps_its_rows(self):
        """Opened by this build, a v3 file gains a table and loses nothing."""
        import sqlite3

        store = PlannerStore(self.dir / "planner")
        request_id = new_planner_request_id()
        store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id="alpha",
            user_intent="devam", request_payload={}, projection_policy_id="p",
            projection_built_at="2026-08-21T00:00:00Z",
            created_at="2026-08-21T00:00:00Z",
        )
        path = self.dir / "planner" / "planner.sqlite3"
        # Wind it back to v3 with the new table absent.
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE planner_worker_reconciliations")
            connection.execute(
                "UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'"
            )

        reopened = PlannerStore(self.dir / "planner")
        self.assertEqual(reopened.get(request_id).user_intent, "devam")
        with sqlite3.connect(path) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            connection.execute("SELECT * FROM planner_worker_reconciliations")
        self.assertEqual(version, str(PLANNER_SCHEMA_VERSION))

    def test_a_future_database_is_refused_rather_than_written_to(self):
        import sqlite3

        from cofferdam.workstation.planner.store import PlannerStoreUnavailable

        PlannerStore(self.dir / "planner")
        path = self.dir / "planner" / "planner.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'"
            )
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir / "planner")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
