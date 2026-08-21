"""Approved prompt → bounded worker: the gate, the isolation, the exact bytes.

The properties under test are the ones a dispatch layer gets wrong quietly:

* a prompt approved and a different prompt run;
* a caller naming a path and reaching another project's repository;
* a crash between task creation and linkage launching a second worker;
* a worker's own "tests passed" quietly becoming Cofferdam's evidence;
* a cancel that reaches somebody else's process.

No worker process is started anywhere in this file. Task Core is real, the
project registry is real, the Git repositories are real; the adapter is a double
that records what it was given, because what is under test is what Cofferdam
decides and hands over rather than what Claude does with it.
"""

from __future__ import annotations

import hashlib
import inspect
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.planner import (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
    AuthorityProvenance,
    PlannerAuthorityService,
    PlannerResult,
    PlannerStore,
    ProviderExecution,
    WorkerDispatchRefused,
    WorkerDispatchService,
    dispatch_request_key,
    new_planner_request_id,
    worker_prompt_digest,
)
from cofferdam.workstation.planner.dispatch import (
    REFUSE_AWAITING,
    REFUSE_NOT_A_PROMPT,
    REFUSE_NOT_SUCCEEDED,
    REFUSE_PROJECT_INELIGIBLE,
    REFUSE_PROJECT_UNRESOLVED,
    REFUSE_REJECTED,
    REFUSE_STALE,
    WORKER_KIND_CLAUDE_CODE,
)
from cofferdam.workstation.planner.dispatch_service import WORKER_ADAPTER_ID
from cofferdam.workstation.planner.store import (
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_SUCCEEDED,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.projects import ProjectRegistry, TaskProject

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
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


class RecordingWorkerAdapter(TaskAdapter):
    """Stands in for the real worker. Records exactly what Cofferdam handed it.

    Starts no process. What matters here is the ``TaskContext`` — the prompt, the
    project id and the server-resolved root — because that is the whole surface
    through which a dispatch could go wrong.
    """

    adapter_id = WORKER_ADAPTER_ID
    display_name = "Recording worker"

    def __init__(self):
        self.contexts = []
        self.cancelled = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(start=True, cancel=True, final_result=True)

    def available(self) -> bool:
        return True

    def start(self, context) -> AdapterOutcome:
        self.contexts.append(context)
        return AdapterOutcome(
            events=(AdapterEvent(text="started").bounded(),),
            requested_state="running",
        )

    def cancel(self, context) -> AdapterOutcome:
        self.cancelled.append(context.task_id)
        return AdapterOutcome(requested_state="cancelled")


class DispatchHarness(unittest.TestCase):
    """Two real projects, a real Task Core, and a real planner database."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

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
        # A project that exists and has not authorized a development worker.
        self.project_readonly = TaskProject(
            project_id="readonly", display_name="Read only",
            root=self.repo_b.resolve(), adapters=("claude-code",),
        )

        self.store = PlannerStore(self.dir / "planner")
        self.authority = PlannerAuthorityService(store=self.store)
        self.who = AuthorityProvenance.internal_test()
        self.adapter = RecordingWorkerAdapter()
        self.tasks = self._task_service()
        self.dispatcher = WorkerDispatchService(store=self.store, tasks=self.tasks)

    def _task_service(self):
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.tasks.adapters import AdapterRegistry
        from cofferdam.workstation.tasks.service import TaskService
        from cofferdam.workstation.tasks.store import TaskStore

        config = load_config(self.dir / "home")
        config.ensure_dirs()
        registry = AdapterRegistry((self.adapter,))
        store = TaskStore(config)
        self.addCleanup(store.close)
        projects = ProjectRegistry(
            projects=(self.project_a, self.project_b, self.project_readonly),
            source_present=True,
        )
        return TaskService(config, store, registry, projects=projects)

    # -- fixtures ------------------------------------------------------------

    def persisted(self, *, action=ACTION_PREPARE_WORKER_PROMPT, status=STATUS_SUCCEEDED,
                  prompt=WORKER_PROMPT, question=None, project_id="alpha") -> str:
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id=project_id,
            user_intent="ilerleyelim", request_payload={},
            projection_policy_id="policy_1", projection_built_at="2026-08-21T00:00:00Z",
            created_at="2026-08-21T00:00:00Z",
        )
        self.store.mark_running(request_id, started_at="2026-08-21T00:00:01Z")
        if status == STATUS_SUCCEEDED:
            self.store.record_success(
                request_id,
                result=PlannerResult(
                    action=action, summary="one step", confidence=0.9,
                    worker_prompt=prompt if action == ACTION_PREPARE_WORKER_PROMPT else None,
                    user_question=question if action == ACTION_ASK_USER else None,
                    decision_basis="context was sufficient",
                ),
                execution=ProviderExecution(
                    provider_id="claude_code", requested_model="opus",
                    actual_model="claude-opus-5",
                ),
                completed_at="2026-08-21T00:00:02Z",
            )
        elif status == STATUS_FAILED:
            self.store.record_failure(
                request_id, failure_code="planner_timeout", failure_message="no answer",
                completed_at="2026-08-21T00:00:02Z",
            )
        elif status == STATUS_INTERRUPTED:
            self.store.mark_interrupted(completed_at="2026-08-21T00:00:02Z")
        return request_id

    def approved(self, **kwargs) -> str:
        request_id = self.persisted(**kwargs)
        gate = self.authority.gate(request_id)
        self.authority.approve_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            provenance=self.who,
        )
        return request_id

    def dispatch(self, request_id):
        return self.dispatcher.dispatch_approved_worker_prompt(
            request_id, provenance=self.who
        )

    def assert_nothing_ran(self):
        self.assertEqual(self.adapter.contexts, [], "a worker was started")
        self.assertEqual(list(self.tasks.list_tasks()), [])


# -- the dispatch API surface -------------------------------------------------


class TheApiCannotCarryAPrompt(DispatchHarness):
    """Approve prompt A, dispatch prompt B — not validated against, unexpressible."""

    def test_dispatch_takes_only_a_planner_request_and_provenance(self):
        parameters = inspect.signature(
            WorkerDispatchService.dispatch_approved_worker_prompt
        ).parameters
        self.assertEqual(set(parameters), {"self", "planner_request_id", "provenance"})

    def test_no_execution_parameter_exists_anywhere_on_the_service(self):
        forbidden = (
            "prompt", "worker_prompt", "command", "argv", "shell", "cwd",
            "repo", "repository", "repo_path", "path", "worktree", "worktree_path",
            "branch", "executable", "model", "tools", "mcp_config", "adapter",
            "adapter_id", "env", "flags",
        )
        for name, method in inspect.getmembers(
            WorkerDispatchService, predicate=inspect.isfunction
        ):
            if name.startswith("__"):
                continue
            parameters = set(inspect.signature(method).parameters)
            for word in forbidden:
                self.assertNotIn(word, parameters, f"{name} accepts {word}")

    def test_the_service_exposes_no_raw_execution_operation(self):
        for forbidden in ("run", "execute", "spawn", "launch", "shell", "run_command"):
            self.assertFalse(hasattr(WorkerDispatchService, forbidden))


# -- the gate ------------------------------------------------------------------


class TheGate(DispatchHarness):
    def test_an_approved_prompt_dispatches(self):
        view = self.dispatch(self.approved())
        self.assertEqual(view.dispatch.adapter_id, WORKER_ADAPTER_ID)
        self.assertEqual(view.dispatch.project_id, "alpha")
        self.assertEqual(len(self.adapter.contexts), 1)

    def test_awaiting_confirmation_is_refused(self):
        request_id = self.persisted()
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_AWAITING)
        self.assert_nothing_ran()

    def test_a_rejected_prompt_is_refused(self):
        request_id = self.persisted()
        gate = self.authority.gate(request_id)
        self.authority.reject_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            reason="wrong scope", provenance=self.who,
        )
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_REJECTED)
        self.assert_nothing_ran()

    def test_an_ask_user_result_is_refused(self):
        request_id = self.persisted(action=ACTION_ASK_USER, question="which database?")
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_NOT_A_PROMPT)
        self.assert_nothing_ran()

    def test_an_answered_ask_user_is_still_not_dispatchable(self):
        """An answer is authority for a question, never for running something."""
        request_id = self.persisted(action=ACTION_ASK_USER, question="which database?")
        gate = self.authority.gate(request_id)
        self.authority.answer_planner_question(
            request_id, answer="postgres",
            expected_subject_fingerprint=gate.subject_fingerprint, provenance=self.who,
        )
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_NOT_A_PROMPT)
        self.assert_nothing_ran()

    def test_a_stop_result_is_refused(self):
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(self.persisted(action=ACTION_STOP, prompt=None))
        self.assertEqual(caught.exception.reason, REFUSE_NOT_A_PROMPT)
        self.assert_nothing_ran()

    def test_a_failed_planner_invocation_is_refused(self):
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(self.persisted(status=STATUS_FAILED))
        self.assertEqual(caught.exception.reason, REFUSE_NOT_SUCCEEDED)
        self.assert_nothing_ran()

    def test_an_interrupted_planner_invocation_is_refused(self):
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(self.persisted(status=STATUS_INTERRUPTED))
        self.assertEqual(caught.exception.reason, REFUSE_NOT_SUCCEEDED)
        self.assert_nothing_ran()

    def test_a_nonexistent_request_is_refused(self):
        with self.assertRaises(WorkerDispatchRefused):
            self.dispatch("plan_nope")
        self.assert_nothing_ran()

    def test_a_changed_prompt_breaks_the_approval_and_refuses(self):
        """The property PR1d's fingerprint exists for, enforced at the launch."""
        import sqlite3

        request_id = self.approved()
        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE planner_requests SET worker_prompt = ? WHERE planner_request_id = ?",
            ("do something else entirely", request_id),
        )
        connection.commit()
        connection.close()

        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_STALE)
        self.assert_nothing_ran()

    def test_a_project_that_does_not_permit_the_worker_is_refused(self):
        request_id = self.approved(project_id="readonly")
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_PROJECT_INELIGIBLE)
        self.assert_nothing_ran()

    def test_an_unresolvable_project_is_refused(self):
        request_id = self.approved(project_id="gamma")
        with self.assertRaises(WorkerDispatchRefused) as caught:
            self.dispatch(request_id)
        self.assertEqual(caught.exception.reason, REFUSE_PROJECT_UNRESOLVED)
        self.assert_nothing_ran()


# -- exact prompt traceability -------------------------------------------------


class ExactPromptTraceability(DispatchHarness):
    """"Worker'a gönderilen prompt buydu" has to be answerable from the bytes."""

    def test_the_worker_receives_the_approved_prompt_byte_for_byte(self):
        request_id = self.approved()
        self.dispatch(request_id)
        delivered = self.adapter.contexts[0].prompt
        self.assertEqual(delivered, WORKER_PROMPT)
        self.assertEqual(
            hashlib.sha256(delivered.encode()).hexdigest(),
            hashlib.sha256(WORKER_PROMPT.encode()).hexdigest(),
        )

    def test_the_chain_is_one_value_from_result_to_worker(self):
        request_id = self.approved()
        view = self.dispatch(request_id)

        persisted = self.store.get(request_id).worker_prompt
        authorized = self.store.authority_event(request_id)
        delivered = self.adapter.contexts[0].prompt

        # result → authority subject → dispatch record → what the worker got.
        self.assertEqual(persisted, WORKER_PROMPT)
        self.assertEqual(delivered, persisted)
        self.assertEqual(
            view.dispatch.worker_prompt_sha256, worker_prompt_digest(persisted)
        )
        self.assertEqual(
            view.dispatch.subject_fingerprint, authorized.subject_fingerprint
        )

    def test_the_dispatched_prompt_is_retrievable_afterwards(self):
        request_id = self.approved()
        self.dispatch(request_id)
        self.assertEqual(self.dispatcher.dispatched_prompt(request_id), WORKER_PROMPT)

    def test_a_prompt_that_changed_after_dispatch_is_not_reported_as_sent(self):
        """The honest answer to "what did you send" is never "what is there now"."""
        import sqlite3

        request_id = self.approved()
        self.dispatch(request_id)
        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE planner_requests SET worker_prompt = ? WHERE planner_request_id = ?",
            ("a prompt nobody dispatched", request_id),
        )
        connection.commit()
        connection.close()
        self.assertIsNone(self.dispatcher.dispatched_prompt(request_id))

    def test_the_execution_contract_does_not_alter_the_semantic_prompt(self):
        from cofferdam.workstation.tasks.adapters.claude_code_worker import (
            build_worker_payload,
            delivered_prompt,
        )
        from cofferdam.workstation.worker.worktree import DevelopmentWorktree

        tree = DevelopmentWorktree(
            project_id="alpha", task_id="task_x", path=Path("/work"),
            branch="cofferdam/worker/task_x", base_commit="a" * 40,
            canonical_root=Path("/repo"),
        )
        payload = build_worker_payload(WORKER_PROMPT, tree)
        self.assertIn(WORKER_PROMPT, payload)
        self.assertEqual(delivered_prompt(payload), WORKER_PROMPT)
        # The contract precedes it and is Cofferdam's own words.
        self.assertTrue(payload.startswith("You are a Cofferdam development worker."))


# -- project identity and two-project isolation --------------------------------


class TwoProjectIsolation(DispatchHarness):
    """Prove B exists and holds something first, then prove A's work never sees it."""

    def test_project_b_really_exists_and_holds_its_own_file(self):
        self.assertTrue((self.repo_b / "PROJECT_B.txt").is_file())
        self.assertFalse((self.repo_a / "PROJECT_B.txt").exists())

    def test_a_dispatch_for_a_resolves_a_and_never_b(self):
        self.dispatch(self.approved(project_id="alpha"))
        context = self.adapter.contexts[0]
        self.assertEqual(context.project_id, "alpha")
        self.assertEqual(Path(context.project_root), self.repo_a.resolve())
        self.assertNotEqual(Path(context.project_root), self.repo_b.resolve())

    def test_the_worker_root_contains_as_marker_and_not_bs(self):
        self.dispatch(self.approved(project_id="alpha"))
        root = Path(self.adapter.contexts[0].project_root)
        self.assertTrue((root / "PROJECT_A.txt").is_file())
        self.assertFalse((root / "PROJECT_B.txt").exists())

    def test_project_b_is_unchanged_by_a_dispatch_for_a(self):
        from cofferdam.workstation.worker import worktree

        before = worktree.canonical_state(self.repo_b.resolve())
        self.dispatch(self.approved(project_id="alpha"))
        self.assertEqual(worktree.canonical_state(self.repo_b.resolve()), before)

    def test_the_persisted_dispatch_records_a(self):
        view = self.dispatch(self.approved(project_id="alpha"))
        self.assertEqual(view.dispatch.project_id, "alpha")
        self.assertEqual(view.to_dict()["dispatch"]["project_id"], "alpha")

    def test_a_caller_cannot_substitute_b_for_a(self):
        """There is no argument to substitute *with*, which is the point."""
        request_id = self.approved(project_id="alpha")
        with self.assertRaises(TypeError):
            self.dispatcher.dispatch_approved_worker_prompt(
                request_id, provenance=self.who, project_id="beta"
            )
        with self.assertRaises(TypeError):
            self.dispatcher.dispatch_approved_worker_prompt(
                request_id, provenance=self.who, cwd=str(self.repo_b)
            )
        self.assert_nothing_ran()

    def test_the_read_model_publishes_no_path(self):
        view = self.dispatch(self.approved(project_id="alpha"))
        rendered = repr(view.to_dict())
        for path in (str(self.repo_a), str(self.repo_b), str(self.dir)):
            self.assertNotIn(path, rendered, "the read model leaked a host path")


# -- idempotency and the crash window ------------------------------------------


class IdempotentDispatch(DispatchHarness):
    def test_the_request_key_is_derived_not_random(self):
        first = dispatch_request_key(
            planner_request_id="plan_a", subject_fingerprint="f" * 64,
            worker_kind=WORKER_KIND_CLAUDE_CODE,
        )
        second = dispatch_request_key(
            planner_request_id="plan_a", subject_fingerprint="f" * 64,
            worker_kind=WORKER_KIND_CLAUDE_CODE,
        )
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 128)

    def test_a_different_approval_produces_a_different_key(self):
        base = dict(planner_request_id="plan_a", worker_kind=WORKER_KIND_CLAUDE_CODE)
        self.assertNotEqual(
            dispatch_request_key(subject_fingerprint="a" * 64, **base),
            dispatch_request_key(subject_fingerprint="b" * 64, **base),
        )

    def test_dispatching_twice_produces_one_worker(self):
        request_id = self.approved()
        first = self.dispatch(request_id)
        second = self.dispatch(request_id)
        self.assertEqual(first.dispatch.dispatch_id, second.dispatch.dispatch_id)
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(len(self.adapter.contexts), 1, "a second worker was started")
        self.assertEqual(len(self.tasks.list_tasks()), 1)

    def test_a_crash_between_task_creation_and_linkage_does_not_launch_twice(self):
        """The actual failure window, reproduced.

        The task is created exactly as a dispatch would create it — same derived
        request key — and then the process "dies" before the linkage row is
        written. The retry must find Task Core's existing task rather than make a
        second one, which is what the derived key buys.
        """
        request_id = self.approved()
        gate = self.authority.gate(request_id)
        key = dispatch_request_key(
            planner_request_id=request_id,
            subject_fingerprint=gate.event.subject_fingerprint,
            worker_kind=WORKER_KIND_CLAUDE_CODE,
        )
        orphan, created = self.tasks.create_task(
            project_id="alpha", adapter_id=WORKER_ADAPTER_ID, prompt=WORKER_PROMPT,
            client_request_id=key, origin="cli", title="Approved development step",
        )
        self.assertTrue(created)
        self.assertIsNone(self.store.dispatch(request_id), "linkage should be absent")

        view = self.dispatch(request_id)

        self.assertEqual(view.task_id, orphan.task_id, "a second task was created")
        self.assertEqual(len(self.tasks.list_tasks()), 1)
        self.assertEqual(len(self.adapter.contexts), 1, "a second worker was started")

    def test_the_orphan_task_is_discoverable_from_its_id(self):
        request_id = self.approved()
        view = self.dispatch(request_id)
        found = self.store.dispatch_by_task(view.task_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.planner_request_id, request_id)

    def test_one_approved_result_yields_at_most_one_dispatch(self):
        import sqlite3

        request_id = self.approved()
        self.dispatch(request_id)
        connection = sqlite3.connect(self.store.path)
        total = connection.execute(
            "SELECT COUNT(*) FROM planner_worker_dispatches"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(total, 1)


# -- what a dispatch is not ----------------------------------------------------


class DispatchIsNotAcceptance(DispatchHarness):
    def test_a_worker_result_is_a_claim_not_evidence(self):
        view = self.dispatch(self.approved())
        payload = view.to_dict()
        self.assertTrue(payload["worker_completion_is_not_acceptance"])

    def test_dispatch_does_not_call_the_planner_again(self):
        """The strongest form: the service has no planner to call."""
        parameters = set(
            inspect.signature(WorkerDispatchService.__init__).parameters
        )
        self.assertEqual(parameters, {"self", "store", "tasks", "workspaces", "clock"})
        for forbidden in ("planner", "provider", "projector", "context"):
            self.assertNotIn(forbidden, parameters)

    def test_dispatch_creates_no_second_planner_request(self):
        import sqlite3

        request_id = self.approved()
        connection = sqlite3.connect(self.store.path)
        before = connection.execute("SELECT COUNT(*) FROM planner_requests").fetchone()[0]
        connection.close()
        self.dispatch(request_id)
        connection = sqlite3.connect(self.store.path)
        after = connection.execute("SELECT COUNT(*) FROM planner_requests").fetchone()[0]
        connection.close()
        self.assertEqual(before, after)

    def test_the_approval_record_is_not_rewritten_by_dispatch(self):
        request_id = self.approved()
        before = self.store.authority_event(request_id)
        self.dispatch(request_id)
        self.assertEqual(self.store.authority_event(request_id), before)

    def test_the_planner_result_is_not_rewritten_by_dispatch(self):
        request_id = self.approved()
        before = self.store.get(request_id).to_dict()
        self.dispatch(request_id)
        self.assertEqual(self.store.get(request_id).to_dict(), before)


# -- cancellation --------------------------------------------------------------


class Cancellation(DispatchHarness):
    def test_cancel_reaches_this_dispatch_and_only_this_one(self):
        first = self.dispatch(self.approved(project_id="alpha"))
        second_request = self.approved(project_id="beta")
        second = self.dispatch(second_request)

        self.dispatcher.cancel(first.dispatch.planner_request_id)

        self.assertEqual(self.adapter.cancelled, [first.task_id])
        self.assertNotIn(second.task_id, self.adapter.cancelled)

    def test_cancelling_an_undispatched_request_is_refused(self):
        with self.assertRaises(WorkerDispatchRefused):
            self.dispatcher.cancel(self.approved())

    def test_the_read_model_says_whether_a_stop_button_applies(self):
        view = self.dispatch(self.approved())
        self.assertIn("cancellable", view.to_dict())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
