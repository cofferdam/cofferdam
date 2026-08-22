"""The two bounded reads behind the remote surface, and what they refuse.

The property this file exists for
---------------------------------

A remote caller names a project **and** a handle. Every test below tries to
break that pairing: a handle from another project, a handle that never existed,
a handle whose grammar is wrong. All three must produce the *same* answer, and
the tests assert that sameness rather than merely asserting each one fails —
a caller that can tell "exists but not yours" from "no such thing" can enumerate
another project's work by watching which error comes back.

Nothing here is allowed to mutate. `NothingHereWrites` snapshots every durable
store around a read and compares.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.operations import reads
from cofferdam.workstation.planner import (
    ACTION_PREPARE_WORKER_PROMPT,
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

ALPHA_PROMPT = "Implement subtract() in calc.py for ALPHA.\n"
BETA_PROMPT = "Rewrite the README for BETA. SECRET-BETA-MATERIAL\n"
ALPHA_CLAIM = "I added subtract() and every test passes."


class Tasks:
    def __init__(self, registry):
        self.projects = registry
        self._rows = {}

    def set(self, task_id, state, *, final_result=None):
        self._rows[task_id] = type(
            "Row", (), {
                "state": state, "task_id": task_id, "final_result": final_result,
                "started_at": "2026-08-22T10:00:00Z", "completed_at": None,
            },
        )()

    def get_task(self, task_id):
        return self._rows.get(task_id)


class ReadsHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        for name in ("alpha", "beta"):
            (self.dir / name).mkdir()
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
        self.tasks = Tasks(self.registry)
        self.who = AuthorityProvenance.internal_test()

        self.alpha = self.chain("alpha", ALPHA_PROMPT)
        self.beta = self.chain("beta", BETA_PROMPT)

    def chain(self, project_id, prompt_text):
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id=project_id,
            user_intent="devam", request_payload={}, projection_policy_id="p",
            projection_built_at="2026-08-22T10:00:00Z",
            created_at="2026-08-22T10:00:00Z",
        )
        self.store.mark_running(request_id, started_at="2026-08-22T10:00:01Z")
        self.store.record_success(
            request_id,
            result=PlannerResult(
                action=ACTION_PREPARE_WORKER_PROMPT, summary=f"work in {project_id}",
                confidence=0.9, worker_prompt=prompt_text,
                decision_basis="context was sufficient",
            ),
            execution=ProviderExecution(provider_id="claude_code"),
            completed_at="2026-08-22T10:00:02Z",
        )
        gate = self.authority.gate(request_id)
        self.authority.approve_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            provenance=self.who,
        )
        task_id = new_task_id()
        dispatch = WorkerDispatch(
            dispatch_id=new_dispatch_id(), planner_request_id=request_id,
            authority_event_id="auth", subject_fingerprint=gate.subject_fingerprint,
            worker_prompt_sha256=worker_prompt_digest(prompt_text),
            project_id=project_id, workspace_id=None,
            adapter_id="claude-code-worker", task_id=task_id,
            request_key=dispatch_request_key(
                planner_request_id=request_id,
                subject_fingerprint=gate.subject_fingerprint,
                worker_kind=WORKER_KIND_CLAUDE_CODE,
            ),
            branch="cofferdam/worker/" + task_id, actor="user",
            source="internal_test", created_at="2026-08-22T10:00:03Z",
        )
        self.store.record_dispatch(dispatch)
        self.tasks.set(task_id, "completed", final_result=ALPHA_CLAIM)
        return dispatch


# -- prompt inspection ---------------------------------------------------------


class ThePromptIsAddressedByProjectAndId(ReadsHarness):
    def test_the_exact_approved_prompt_comes_back(self):
        found = reads.prompt(
            self.store, project_id="alpha",
            planner_request_id=self.alpha.planner_request_id,
        )
        self.assertEqual(found.prompt, ALPHA_PROMPT)
        self.assertFalse(found.truncated)

    def test_it_is_verified_against_the_dispatched_digest(self):
        """'What did you send' is never answered with 'what is there now'."""
        found = reads.prompt(
            self.store, project_id="alpha",
            planner_request_id=self.alpha.planner_request_id,
        )
        self.assertTrue(found.verified)
        self.assertTrue(found.to_dict()["matches_dispatched_digest"])

    def test_a_prompt_that_no_longer_matches_is_reported_as_such(self):
        """Not hidden, and not silently returned as though it were the original."""
        import sqlite3

        with sqlite3.connect(self.dir / "planner" / "planner.sqlite3") as connection:
            connection.execute(
                "UPDATE planner_requests SET worker_prompt = ? "
                "WHERE planner_request_id = ?",
                ("tampered\n", self.alpha.planner_request_id),
            )
        found = reads.prompt(
            self.store, project_id="alpha",
            planner_request_id=self.alpha.planner_request_id,
        )
        self.assertFalse(found.verified)
        self.assertEqual(found.prompt, "tampered\n")

    def test_it_carries_the_approved_fingerprint(self):
        found = reads.prompt(
            self.store, project_id="alpha",
            planner_request_id=self.alpha.planner_request_id,
        )
        self.assertEqual(
            found.approved_fingerprint, self.alpha.subject_fingerprint
        )

    def test_a_long_prompt_is_bounded_and_says_so(self):
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id="alpha",
            user_intent="x", request_payload={}, projection_policy_id="p",
            projection_built_at="t", created_at="t",
        )
        self.store.mark_running(request_id, started_at="t")
        self.store.record_success(
            request_id,
            result=PlannerResult(
                action=ACTION_PREPARE_WORKER_PROMPT, summary="s", confidence=0.9,
                worker_prompt="y" * (reads.MAX_PROMPT_CHARS + 500),
                decision_basis="b",
            ),
            execution=ProviderExecution(provider_id="claude_code"),
            completed_at="t",
        )
        found = reads.prompt(
            self.store, project_id="alpha", planner_request_id=request_id
        )
        self.assertTrue(found.truncated)
        self.assertEqual(len(found.prompt), reads.MAX_PROMPT_CHARS)


class ForeignAndMissingHandlesLookIdentical(ReadsHarness):
    """The enumeration property, asserted as sameness rather than as failure."""

    def refusal(self, project_id, planner_request_id):
        with self.assertRaises(reads.OperationsNotFound) as caught:
            reads.prompt(
                self.store, project_id=project_id,
                planner_request_id=planner_request_id,
            )
        return caught.exception

    def test_betas_prompt_is_not_readable_under_alpha(self):
        exc = self.refusal("alpha", self.beta.planner_request_id)
        self.assertNotIn("SECRET-BETA-MATERIAL", str(exc))

    def test_a_foreign_handle_and_a_missing_one_give_the_same_message(self):
        foreign = self.refusal("alpha", self.beta.planner_request_id)
        missing = self.refusal("alpha", new_planner_request_id())
        self.assertEqual(str(foreign), str(missing))

    def test_alphas_prompt_is_not_readable_under_beta_either(self):
        with self.assertRaises(reads.OperationsNotFound):
            reads.prompt(
                self.store, project_id="beta",
                planner_request_id=self.alpha.planner_request_id,
            )

    def test_each_project_can_still_read_its_own(self):
        """Without this, the isolation tests above would pass vacuously."""
        self.assertEqual(
            reads.prompt(self.store, project_id="alpha",
                         planner_request_id=self.alpha.planner_request_id).prompt,
            ALPHA_PROMPT,
        )
        self.assertEqual(
            reads.prompt(self.store, project_id="beta",
                         planner_request_id=self.beta.planner_request_id).prompt,
            BETA_PROMPT,
        )

    def test_a_request_with_no_prompt_is_not_found_rather_than_empty(self):
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id="alpha",
            user_intent="x", request_payload={}, projection_policy_id="p",
            projection_built_at="t", created_at="t",
        )
        with self.assertRaises(reads.OperationsNotFound):
            reads.prompt(self.store, project_id="alpha",
                         planner_request_id=request_id)

    def test_there_is_no_fallback_to_the_latest_prompt(self):
        """A stale handle must not quietly return a different operation's text."""
        with self.assertRaises(reads.OperationsNotFound):
            reads.prompt(self.store, project_id="alpha",
                         planner_request_id="plan_00000000000000000000000000")


# -- evidence ------------------------------------------------------------------


class EvidenceKeepsMachineAndClaimApart(ReadsHarness):
    def setUp(self):
        super().setUp()
        self.store.record_reconciliation(
            DispatchReconciliation(
                dispatch_id=self.alpha.dispatch_id, task_id=self.alpha.task_id,
                project_id="alpha", outcome="commit_recovered",
                recovered_commit="a" * 40, checks_observed=1, check_exit_zero=1,
                worktree_retained=1, reconciled_at="2026-08-22T10:10:00Z",
            )
        )
        self.store.upsert_publication(
            Publication(
                publication_id="pub_1", dispatch_id=self.alpha.dispatch_id,
                planner_request_id=self.alpha.planner_request_id,
                task_id=self.alpha.task_id, project_id="alpha", workspace_id=None,
                repository="cofferdam/publisher-smoke", branch=self.alpha.branch,
                base_branch="main", commit_sha="b" * 40, state="published",
                pull_request_number=5,
                pull_request_url="https://github.com/cofferdam/publisher-smoke/pull/5",
                pull_request_state="open", actor="cofferdam", source="publisher",
                created_at="t", updated_at="t",
            )
        )
        self.found = reads.result(
            self.store, self.tasks, project_id="alpha",
            dispatch_id=self.alpha.dispatch_id,
        )

    def test_machine_facts_are_cofferdam_observed(self):
        machine = self.found.machine
        self.assertEqual(machine["observed_by"], "cofferdam")
        self.assertEqual(machine["branch"], self.alpha.branch)
        self.assertTrue(machine["checks"]["observed"])
        self.assertIs(machine["checks"]["exit_zero"], True)

    def test_the_published_commit_wins_over_the_recovered_one(self):
        """Both are real; the published one is what actually reached GitHub."""
        self.assertEqual(self.found.machine["commit"], "b" * 40)

    def test_the_pull_request_is_a_machine_fact(self):
        self.assertEqual(
            self.found.machine["publication"]["pull_request"]["number"], 5
        )

    def test_the_worker_report_is_a_claim(self):
        self.assertEqual(self.found.claims["worker_report"], ALPHA_CLAIM)
        self.assertEqual(self.found.claims["source"], "model_authored")

    def test_no_claim_appears_in_the_machine_block(self):
        self.assertNotIn(ALPHA_CLAIM, json.dumps(self.found.machine))

    def test_success_is_never_invented_from_prose(self):
        """'Every test passes' does not make `exit_zero` true."""
        beta_result = reads.result(
            self.store, self.tasks, project_id="beta",
            dispatch_id=self.beta.dispatch_id,
        )
        self.assertIsNone(beta_result.machine["checks"])
        self.assertIsNone(beta_result.machine["commit"])
        self.assertIsNone(beta_result.machine["publication"])
        self.assertEqual(beta_result.claims["worker_report"], ALPHA_CLAIM)

    def test_worker_completion_is_not_acceptance_is_stated(self):
        self.assertTrue(self.found.to_dict()["worker_completion_is_not_acceptance"])

    def test_betas_evidence_is_not_readable_under_alpha(self):
        with self.assertRaises(reads.OperationsNotFound):
            reads.result(self.store, self.tasks, project_id="alpha",
                           dispatch_id=self.beta.dispatch_id)

    def test_a_missing_dispatch_and_a_foreign_one_look_the_same(self):
        with self.assertRaises(reads.OperationsNotFound) as foreign:
            reads.result(self.store, self.tasks, project_id="alpha",
                           dispatch_id=self.beta.dispatch_id)
        with self.assertRaises(reads.OperationsNotFound) as missing:
            reads.result(self.store, self.tasks, project_id="alpha",
                           dispatch_id=new_dispatch_id())
        self.assertEqual(str(foreign.exception), str(missing.exception))

    def test_the_payload_carries_no_host_path_or_credential(self):
        rendered = json.dumps(self.found.to_dict())
        self.assertNotIn(str(self.dir), rendered)
        self.assertNotIn("/home/", rendered)
        for forbidden in ("token", "credential", "git-credentials", "sk-ant",
                          "github_pat_"):
            self.assertNotIn(forbidden, rendered, forbidden)

    def test_it_carries_no_remote_url(self):
        rendered = json.dumps(self.found.to_dict())
        self.assertNotIn("github.com/cofferdam/publisher-smoke.git", rendered)
        self.assertNotIn("git@github.com", rendered)


# -- nothing writes ------------------------------------------------------------


class NothingHereWrites(ReadsHarness):
    """Read routes grant no write authority, asserted against the databases."""

    def snapshot(self):
        import sqlite3

        with sqlite3.connect(self.dir / "planner" / "planner.sqlite3") as connection:
            tables = [
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            return {
                name: connection.execute(
                    f"SELECT * FROM {name}"  # noqa: S608 - names from sqlite_master
                ).fetchall()
                for name in tables
            }

    def test_reading_a_prompt_changes_nothing(self):
        before = self.snapshot()
        reads.prompt(self.store, project_id="alpha",
                     planner_request_id=self.alpha.planner_request_id)
        self.assertEqual(self.snapshot(), before)

    def test_reading_evidence_changes_nothing(self):
        before = self.snapshot()
        reads.result(self.store, self.tasks, project_id="alpha",
                       dispatch_id=self.alpha.dispatch_id)
        self.assertEqual(self.snapshot(), before)

    def test_a_refused_read_changes_nothing(self):
        before = self.snapshot()
        with self.assertRaises(reads.OperationsNotFound):
            reads.prompt(self.store, project_id="alpha",
                         planner_request_id=self.beta.planner_request_id)
        self.assertEqual(self.snapshot(), before)

    def test_the_module_exposes_no_writer(self):
        for forbidden in ("approve", "reject", "answer", "cancel", "publish",
                          "dispatch", "write", "update", "delete"):
            self.assertFalse(hasattr(reads, forbidden), forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
