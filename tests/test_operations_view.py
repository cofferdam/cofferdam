"""What is Cofferdam doing right now? Asked of the real components.

The three properties this file is built to protect
---------------------------------------------------

**The view owns nothing.** Every phase is derived on each read from the planner,
the authority gate, Task Core, worker recovery and the publisher. So the tests
below change a *durable row* and assert the phase moves — never the other way
round. There is no setter to call, and a test that needed one would be evidence
the projection had grown a lifetime of its own.

**Projects do not mix.** Two projects are driven to different phases in the same
harness, and the assertions check that neither one's dispatch, commit, branch or
pull request appears in the other's payload.

**Machine facts and model claims stay apart.** A worker's prose is planted where
a careless join would pick it up, and the tests assert it lands in `claims`,
never in `machine`, and never changes a phase.

Real planner and publisher databases throughout; Task Core is a small double
because what is under test is the *projection*, not the lifecycle.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.operations import OperationsService, phases
from cofferdam.workstation.operations import view as opsview
from cofferdam.workstation.planner import (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
    AuthorityProvenance,
    PlannerAuthorityService,
    PlannerResult,
    PlannerStore,
    ProviderExecution,
    new_planner_request_id,
)
from cofferdam.workstation.planner.dispatch import (
    WORKER_KIND_CLAUDE_CODE,
    dispatch_request_key,
    new_dispatch_id,
    worker_prompt_digest,
)
from cofferdam.workstation.planner.store import (
    DispatchReconciliation,
    Publication,
    WorkerDispatch,
)
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.tasks.projects import ProjectRegistry, TaskProject

WORKER_PROMPT = "Implement subtract() in calc.py and add a test.\n"
WORKER_CLAIM = "I added subtract() and every test passes."


class TaskDouble:
    """Task Core's answer, settable per task. Not the thing under test."""

    def __init__(self, registry):
        self.projects = registry
        self._rows = {}

    def set(self, task_id, state, *, final_result=None):
        self._rows[task_id] = type(
            "Row", (), {
                "state": state, "task_id": task_id, "final_result": final_result,
                "started_at": "2026-08-22T09:00:00Z", "completed_at": None,
            },
        )()

    def get_task(self, task_id):
        return self._rows.get(task_id)


class OperationsHarness(unittest.TestCase):
    """Two real projects, one real planner database, one projection."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        (self.dir / "alpha").mkdir()
        (self.dir / "beta").mkdir()
        self.registry = ProjectRegistry(
            projects=(
                TaskProject(project_id="alpha", display_name="Alpha",
                            root=(self.dir / "alpha").resolve(),
                            adapters=("claude-code-worker",)),
                TaskProject(project_id="beta", display_name="Beta",
                            root=(self.dir / "beta").resolve(),
                            adapters=("claude-code-worker",)),
            ),
            source_present=True,
        )
        self.store = PlannerStore(self.dir / "planner")
        self.authority = PlannerAuthorityService(store=self.store)
        self.tasks = TaskDouble(self.registry)
        self.ops = OperationsService(planner_store=self.store, tasks=self.tasks)
        self.who = AuthorityProvenance.internal_test()

    # -- fixture builders, each advancing one project one step ------------

    def planner_running(self, project_id="alpha"):
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id=project_id,
            user_intent="devam", request_payload={}, projection_policy_id="p",
            projection_built_at="2026-08-22T09:00:00Z",
            created_at="2026-08-22T09:00:00Z",
        )
        self.store.mark_running(request_id, started_at="2026-08-22T09:00:01Z")
        return request_id

    def planner_done(self, project_id="alpha", action=ACTION_PREPARE_WORKER_PROMPT,
                     summary="add subtract()"):
        request_id = self.planner_running(project_id)
        self.store.record_success(
            request_id,
            result=PlannerResult(
                action=action, summary=summary, confidence=0.9,
                worker_prompt=WORKER_PROMPT if action == ACTION_PREPARE_WORKER_PROMPT else None,
                user_question="which module?" if action == ACTION_ASK_USER else None,
                decision_basis="context was sufficient",
            ),
            execution=ProviderExecution(provider_id="claude_code"),
            completed_at="2026-08-22T09:00:02Z",
        )
        return request_id

    def approved(self, project_id="alpha"):
        request_id = self.planner_done(project_id)
        gate = self.authority.gate(request_id)
        self.authority.approve_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            provenance=self.who,
        )
        return request_id, gate.subject_fingerprint

    def rejected(self, project_id="alpha"):
        request_id = self.planner_done(project_id)
        gate = self.authority.gate(request_id)
        self.authority.reject_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            provenance=self.who, reason="not now",
        )
        return request_id

    def dispatched(self, project_id="alpha", task_state="running"):
        request_id, fingerprint = self.approved(project_id)
        task_id = new_task_id()
        dispatch = WorkerDispatch(
            dispatch_id=new_dispatch_id(), planner_request_id=request_id,
            authority_event_id="auth", subject_fingerprint=fingerprint,
            worker_prompt_sha256=worker_prompt_digest(WORKER_PROMPT),
            project_id=project_id, workspace_id=None,
            adapter_id="claude-code-worker", task_id=task_id,
            request_key=dispatch_request_key(
                planner_request_id=request_id, subject_fingerprint=fingerprint,
                worker_kind=WORKER_KIND_CLAUDE_CODE,
            ),
            branch="cofferdam/worker/" + task_id, actor="user",
            source="internal_test", created_at="2026-08-22T09:00:03Z",
        )
        self.store.record_dispatch(dispatch)
        self.tasks.set(task_id, task_state, final_result=WORKER_CLAIM)
        return dispatch

    def reconciled(self, dispatch, outcome, *, needs_attention=0, commit=None):
        self.store.record_reconciliation(
            DispatchReconciliation(
                dispatch_id=dispatch.dispatch_id, task_id=dispatch.task_id,
                project_id=dispatch.project_id, outcome=outcome,
                recovered_commit=commit, needs_attention=needs_attention,
                worktree_retained=1, checks_observed=1, check_exit_zero=1,
                reconciled_at="2026-08-22T09:10:00Z",
            )
        )

    def published(self, dispatch, state, *, number=None, failure=None):
        self.store.upsert_publication(
            Publication(
                publication_id="pub_" + dispatch.task_id[5:],
                dispatch_id=dispatch.dispatch_id,
                planner_request_id=dispatch.planner_request_id,
                task_id=dispatch.task_id, project_id=dispatch.project_id,
                workspace_id=None, repository="cofferdam/publisher-smoke",
                branch=dispatch.branch, base_branch="main",
                commit_sha="a" * 40, state=state,
                pull_request_number=number,
                pull_request_url=(
                    None if number is None
                    else f"https://github.com/cofferdam/publisher-smoke/pull/{number}"
                ),
                pull_request_state="open" if number else None,
                failure_reason=failure, needs_attention=int(state != "published"),
                actor="cofferdam", source="publisher",
                created_at="2026-08-22T09:20:00Z", updated_at="2026-08-22T09:20:00Z",
            )
        )

    def phase_of(self, project_id="alpha"):
        return self.ops.project(project_id).phase.phase


# -- the projection, one case per truthful state ------------------------------


class ThePhaseIsProjectedFromDurableFacts(OperationsHarness):
    """Change a row, and the phase moves. There is no setter."""

    def test_a_project_with_nothing_is_idle(self):
        self.assertEqual(self.phase_of(), phases.PHASE_IDLE)

    def test_a_running_planner_is_preparing(self):
        self.planner_running()
        self.assertEqual(self.phase_of(), phases.PHASE_PLANNER_PREPARING)

    def test_a_question_awaits_the_user(self):
        self.planner_done(action=ACTION_ASK_USER)
        self.assertEqual(self.phase_of(), phases.PHASE_AWAITING_USER_ANSWER)

    def test_a_prepared_prompt_awaits_approval(self):
        self.planner_done()
        self.assertEqual(self.phase_of(), phases.PHASE_AWAITING_APPROVAL)

    def test_a_planner_stop_is_not_a_failure(self):
        self.planner_done(action=ACTION_STOP)
        self.assertEqual(self.phase_of(), phases.PHASE_STOPPED)
        self.assertFalse(self.ops.project("alpha").phase.needs_person)

    def test_a_rejection_is_the_users_decision(self):
        self.rejected()
        self.assertEqual(self.phase_of(), phases.PHASE_REJECTED)

    def test_an_approved_prompt_with_no_task_is_queued(self):
        self.approved()
        self.assertEqual(self.phase_of(), phases.PHASE_WORKER_QUEUED)

    def test_a_queued_task_is_queued(self):
        self.dispatched(task_state="queued")
        self.assertEqual(self.phase_of(), phases.PHASE_WORKER_QUEUED)

    def test_a_running_worker_is_running(self):
        self.dispatched(task_state="running")
        self.assertEqual(self.phase_of(), phases.PHASE_WORKER_RUNNING)

    def test_a_cancelled_task_is_cancelled(self):
        self.dispatched(task_state="cancelled")
        self.assertEqual(self.phase_of(), phases.PHASE_CANCELLED)

    def test_a_failed_task_is_failed(self):
        self.dispatched(task_state="failed")
        self.assertEqual(self.phase_of(), phases.PHASE_FAILED)

    def test_a_parked_task_is_recovery_required(self):
        self.dispatched(task_state="recovery_required")
        self.assertEqual(self.phase_of(), phases.PHASE_RECOVERY_REQUIRED)

    def test_preserved_partial_work_is_interrupted_not_failed(self):
        """The distinction the whole recovery milestone exists for."""
        dispatch = self.dispatched(task_state="interrupted")
        self.reconciled(dispatch, "partial_work_preserved", needs_attention=1)
        self.assertEqual(self.phase_of(), phases.PHASE_WORKER_INTERRUPTED)
        self.assertNotEqual(self.phase_of(), phases.PHASE_FAILED)

    def test_a_recovered_commit_surfaces_as_publishable(self):
        """Saying 'interrupted' would bury the thing a person can act on."""
        dispatch = self.dispatched(task_state="interrupted")
        self.reconciled(dispatch, "commit_recovered", commit="b" * 40)
        self.assertEqual(self.phase_of(), phases.PHASE_COMMIT_READY)
        self.assertIn(opsview.ACTION_PUBLISH, self.ops.project("alpha").actions)

    def test_a_contradictory_reconciliation_needs_attention(self):
        dispatch = self.dispatched(task_state="interrupted")
        self.reconciled(dispatch, "contradictory", needs_attention=1)
        self.assertEqual(self.phase_of(), phases.PHASE_NEEDS_ATTENTION)

    def test_a_clean_restart_with_nothing_done_is_reconciled(self):
        dispatch = self.dispatched(task_state="interrupted")
        self.reconciled(dispatch, "no_work_found")
        self.assertEqual(self.phase_of(), phases.PHASE_RECOVERY_RECONCILED)

    def test_publishing_is_visible_while_it_happens(self):
        dispatch = self.dispatched(task_state="completed")
        self.published(dispatch, "pending")
        self.assertEqual(self.phase_of(), phases.PHASE_PUBLISHING)
        self.published(dispatch, "branch_published")
        self.assertEqual(self.phase_of(), phases.PHASE_PUBLISHING)

    def test_an_open_pull_request_is_the_end_state(self):
        dispatch = self.dispatched(task_state="completed")
        self.published(dispatch, "published", number=7)
        found = self.ops.project("alpha")
        self.assertEqual(found.phase.phase, phases.PHASE_PR_READY)
        self.assertTrue(found.phase.settled)
        self.assertEqual(found.machine["publication"]["pull_request"]["number"], 7)

    def test_a_publishing_auth_failure_is_its_own_phase(self):
        """Not a worker failure. The commit is safe and only sending failed."""
        dispatch = self.dispatched(task_state="completed")
        self.published(dispatch, "refused", failure="publisher_auth_required")
        found = self.ops.project("alpha")
        self.assertEqual(found.phase.phase, phases.PHASE_AUTH_REQUIRED)
        self.assertEqual(found.actions, (opsview.ACTION_SIGN_IN,))

    def test_a_completed_worker_with_no_commit_is_not_commit_ready(self):
        """`commit_all` returning None is a real outcome, not a commit."""
        self.dispatched(task_state="completed")
        self.assertNotEqual(self.phase_of(), phases.PHASE_COMMIT_READY)
        self.assertEqual(self.phase_of(), phases.PHASE_RECOVERY_RECONCILED)

    def test_the_latest_fact_wins_over_an_earlier_one(self):
        """A published dispatch is not 'awaiting approval' because a row exists."""
        dispatch = self.dispatched(task_state="completed")
        self.published(dispatch, "published", number=9)
        self.assertEqual(self.phase_of(), phases.PHASE_PR_READY)
        self.assertTrue(self.ops.project("alpha").machine["approved"])

    def test_every_phase_names_the_fact_that_produced_it(self):
        self.dispatched(task_state="running")
        because = self.ops.project("alpha").phase.because
        self.assertIn("task.state=running", because)


# -- no state of its own ------------------------------------------------------


class TheViewOwnsNothing(OperationsHarness):
    def test_there_is_no_writer_on_the_service(self):
        for forbidden in ("set_phase", "record", "save", "update", "write", "store_phase"):
            self.assertFalse(hasattr(self.ops, forbidden), forbidden)

    def test_no_operations_table_was_created(self):
        import sqlite3

        with sqlite3.connect(self.dir / "planner" / "planner.sqlite3") as connection:
            names = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        for forbidden in ("operations", "operations_view", "project_status", "phases"):
            self.assertNotIn(forbidden, names)

    def test_the_same_read_twice_reflects_a_change_in_between(self):
        """Proof the phase is derived, not cached."""
        dispatch = self.dispatched(task_state="running")
        self.assertEqual(self.phase_of(), phases.PHASE_WORKER_RUNNING)
        self.tasks.set(dispatch.task_id, "completed", final_result=WORKER_CLAIM)
        self.published(dispatch, "published", number=3)
        self.assertEqual(self.phase_of(), phases.PHASE_PR_READY)

    def test_a_fresh_service_gives_the_same_answer(self):
        dispatch = self.dispatched(task_state="completed")
        self.published(dispatch, "published", number=4)
        rebuilt = OperationsService(
            planner_store=PlannerStore(self.dir / "planner"), tasks=self.tasks
        )
        self.assertEqual(
            rebuilt.project("alpha").to_dict(), self.ops.project("alpha").to_dict()
        )


# -- projects do not mix ------------------------------------------------------


class TwoProjectsStaySeparate(OperationsHarness):
    def setUp(self):
        super().setUp()
        self.alpha = self.dispatched("alpha", task_state="running")
        self.beta = self.dispatched("beta", task_state="completed")
        # A pull request number that cannot occur by coincidence. `11` was here
        # and made the substring assertion below time-dependent: `approved_at`
        # is a live UTC timestamp, so any minute, second, hour or day rendering
        # as `11` matched it and the test failed for roughly one minute in
        # sixty. Reproduced on `main` by freezing the clock at 00:11:09 — the
        # flake predates M2M PR4 and is fixed here because it fires on any PR.
        self.published(self.beta, "published", number=907341)

    def test_each_project_reports_its_own_phase(self):
        self.assertEqual(self.phase_of("alpha"), phases.PHASE_WORKER_RUNNING)
        self.assertEqual(self.phase_of("beta"), phases.PHASE_PR_READY)

    def test_neither_payload_carries_the_others_identifiers(self):
        a = json.dumps(self.ops.project("alpha").to_dict())
        b = json.dumps(self.ops.project("beta").to_dict())
        self.assertNotIn(self.beta.task_id, a)
        self.assertNotIn(self.beta.dispatch_id, a)
        self.assertNotIn(self.beta.branch, a)
        self.assertNotIn(self.alpha.task_id, b)
        self.assertNotIn(self.alpha.dispatch_id, b)

    def test_beta_s_pull_request_does_not_appear_under_alpha(self):
        alpha = self.ops.project("alpha")
        self.assertIsNone(alpha.machine["publication"])
        self.assertNotIn("907341", json.dumps(alpha.machine))

    def test_the_overview_lists_both_without_merging_them(self):
        overview = self.ops.overview()
        self.assertEqual({item.project_id for item in overview}, {"alpha", "beta"})
        by_id = {item.project_id: item for item in overview}
        self.assertEqual(by_id["alpha"].phase.phase, phases.PHASE_WORKER_RUNNING)
        self.assertEqual(by_id["beta"].phase.phase, phases.PHASE_PR_READY)

    def test_the_overview_leads_with_what_needs_a_person(self):
        """A project waiting on you outranks one that is merely running."""
        self.tasks.set(self.alpha.task_id, "recovery_required")
        overview = self.ops.overview()
        self.assertEqual(overview[0].project_id, "alpha")
        self.assertTrue(overview[0].phase.needs_person)

    def test_an_unknown_project_is_idle_rather_than_an_error(self):
        found = self.ops.project("nonexistent")
        self.assertEqual(found.phase.phase, phases.PHASE_IDLE)


# -- machine facts vs model claims --------------------------------------------


class ClaimsNeverBecomeObservations(OperationsHarness):
    def setUp(self):
        super().setUp()
        self.dispatch = self.dispatched(task_state="completed")
        self.reconciled(self.dispatch, "commit_recovered", commit="c" * 40)
        self.found = self.ops.project("alpha")

    def test_the_worker_report_is_a_claim(self):
        self.assertEqual(self.found.claims["worker_report"], WORKER_CLAIM)
        self.assertEqual(self.found.claims["source"], "model_authored")

    def test_no_claim_leaks_into_the_machine_block(self):
        rendered = json.dumps(self.found.machine)
        self.assertNotIn(WORKER_CLAIM, rendered)
        self.assertNotIn("every test passes", rendered)

    def test_a_worker_claiming_success_does_not_move_the_phase(self):
        """The claim says every test passes. The phase comes from the rows.

        A second project is used rather than rewriting alpha's reconciliation:
        PR1f records the *first* post-crash answer and ignores later ones, so
        overwriting one is not a thing a test may do -- it would be asserting
        against behaviour the recovery milestone deliberately does not have.
        """
        beta = self.dispatched("beta", task_state="failed")
        self.tasks.set(beta.task_id, "failed", final_result=WORKER_CLAIM)
        self.reconciled(beta, "contradictory", needs_attention=1)
        found = self.ops.project("beta")
        self.assertEqual(found.claims["worker_report"], WORKER_CLAIM)
        self.assertEqual(found.phase.phase, phases.PHASE_NEEDS_ATTENTION)

    def test_the_check_result_is_a_machine_observation(self):
        self.assertTrue(self.found.machine["restart"]["checks_observed"])
        self.assertIs(self.found.machine["restart"]["check_exit_zero"], True)

    def test_worker_completion_is_not_acceptance_is_always_stated(self):
        for project_id in ("alpha", "beta"):
            self.assertTrue(
                self.ops.project(project_id).machine[
                    "worker_completion_is_not_acceptance"
                ]
            )

    def test_a_long_claim_is_bounded(self):
        self.tasks.set(self.dispatch.task_id, "completed", final_result="x" * 5000)
        report = self.ops.project("alpha").claims["worker_report"]
        self.assertLessEqual(len(report), 601)


# -- the safe shape -----------------------------------------------------------


class TheReadModelIsSafeToSend(OperationsHarness):
    def setUp(self):
        super().setUp()
        self.dispatch = self.dispatched(task_state="completed")
        self.published(self.dispatch, "published", number=13)
        self.payload = self.ops.project("alpha").to_dict()

    def test_it_serializes(self):
        json.dumps(self.payload)
        json.dumps([item.to_dict() for item in self.ops.overview()])

    def test_it_carries_no_host_path(self):
        rendered = json.dumps(self.payload)
        self.assertNotIn(str(self.dir), rendered)
        self.assertNotIn("/home/", rendered)
        self.assertNotIn(str(Path.home()), rendered)

    def test_it_carries_no_credential_shaped_text(self):
        rendered = json.dumps(self.payload)
        for forbidden in ("github_pat_", "ghp_", "sk-ant-", "accessToken",
                          "refreshToken", "credential", "token"):
            self.assertNotIn(forbidden, rendered, forbidden)

    def test_it_carries_no_remote_url(self):
        rendered = json.dumps(self.payload)
        self.assertNotIn("https://github.com/cofferdam/publisher-smoke.git", rendered)
        self.assertNotIn("git@github.com", rendered)

    def test_the_prompt_is_not_in_a_status_read(self):
        """Retrievable, but not carried by every poll."""
        rendered = json.dumps(self.payload)
        self.assertNotIn(WORKER_PROMPT.strip(), rendered)
        self.assertTrue(self.payload["handles"]["prompt_available"])

    def test_the_handles_address_every_later_action(self):
        handles = self.payload["handles"]
        self.assertEqual(handles["dispatch_id"], self.dispatch.dispatch_id)
        self.assertEqual(handles["task_id"], self.dispatch.task_id)
        self.assertEqual(
            handles["planner_request_id"], self.dispatch.planner_request_id
        )
        self.assertIsNotNone(handles["publication_id"])

    def test_the_pull_request_is_addressable_without_a_latest_lookup(self):
        pull_request = self.payload["machine"]["publication"]["pull_request"]
        self.assertEqual(pull_request["number"], 13)
        self.assertIn("/pull/13", pull_request["url"])


# -- the vocabulary itself ----------------------------------------------------


class ThePhaseVocabularyIsComplete(unittest.TestCase):
    def test_every_phase_has_a_sentence(self):
        for phase in phases.PHASES:
            self.assertIn(phase, phases.SENTENCES, phase)
            self.assertTrue(phases.SENTENCES[phase].strip())

    def test_no_sentence_is_merely_a_status_word(self):
        """A cockpit must be able to say more than 'failed'."""
        for phase, sentence in phases.SENTENCES.items():
            self.assertGreater(len(sentence.split()), 2, phase)
            self.assertNotEqual(sentence.strip().lower(), phase.replace("_", " "))

    def test_every_phase_has_an_action_set(self):
        for phase in phases.PHASES:
            self.assertIn(phase, opsview.AVAILABLE_ACTIONS, phase)

    def test_every_phase_has_a_distinct_rank(self):
        ranks = [phases.rank(phase) for phase in phases.PHASES]
        self.assertEqual(len(set(ranks)), len(ranks), "two phases sort equally")

    def test_the_attention_phases_all_rank_above_the_busy_ones(self):
        worst_attention = max(phases.rank(p) for p in phases.NEEDS_PERSON)
        best_busy = min(
            phases.rank(p) for p in phases.BUSY if p not in phases.NEEDS_PERSON
        )
        self.assertLess(worst_attention, best_busy)

    def test_no_phase_is_both_busy_and_settled(self):
        self.assertEqual(phases.BUSY & phases.SETTLED, frozenset())

    def test_the_action_vocabulary_is_closed(self):
        known = {
            opsview.ACTION_ANSWER, opsview.ACTION_APPROVE, opsview.ACTION_REJECT,
            opsview.ACTION_CANCEL, opsview.ACTION_INSPECT_PROMPT,
            opsview.ACTION_INSPECT_RESULT, opsview.ACTION_PUBLISH,
            opsview.ACTION_OPEN_PULL_REQUEST, opsview.ACTION_RECONCILE,
            opsview.ACTION_SIGN_IN,
        }
        for actions in opsview.AVAILABLE_ACTIONS.values():
            for action in actions:
                self.assertIn(action, known, action)

    def test_the_view_implements_no_control(self):
        """It reports what would apply. It cannot do any of it."""
        for forbidden in ("answer", "approve", "reject", "cancel", "publish",
                          "dispatch", "stop"):
            self.assertFalse(hasattr(OperationsService, forbidden), forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
