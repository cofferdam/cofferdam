"""Same-session follow-up, provider turns, and the ``get_result`` boundary.

Standard library only. Nothing here starts a process, opens a socket, imports the
Claude Agent SDK or reaches Anthropic — the helper-process boundary and the
injected session double are what make that possible, exactly as they do for the
clarification suite.

What is proven here
-------------------

*A task can be several turns, and each turn's evidence survives the next.* The
result somebody read before sending a follow-up is still readable afterwards,
because a completed turn row is never written again.

*``get_result`` has one meaning and says so.* The latest completed turn's result,
with ``task_terminal`` distinguishing a task that may still produce more from one
that is finished — asserted in both directions rather than left to a reader.

*A follow-up is not an answer and not an approval.* Three concepts, three code
paths, and a body shaped like one of the others is refused by the route it was
sent to.

*The same session continues.* One client, one provider session id, ordered turns,
and a late event from a settled turn that cannot complete the current one.

*Restart is truthful.* A follow-up after the daemon has gone is refused as
unavailable, and the result the task produced before it went is still there.

What is **not** proven here, said plainly: that a real Claude session accepts a
second ``query()`` on a live client and continues the same conversation. The SDK
source says it does — a result frame ends one turn, ``receive_messages()``
continues past it, and ``connect()`` without a prompt never closes stdin — and
these tests hold Cofferdam to that contract against a double. Settling it against
the provider needs a supervised live spike.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import unittest
from typing import Any, Dict, List, Optional, Sequence

from ._task_doubles import PROJECT_ID, TURKISH_PROMPT, TaskTestCase

from cofferdam.workstation.tasks import errors as task_errors
from cofferdam.workstation.tasks import store as store_module
from cofferdam.workstation.tasks import turns as turns_module
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.clarifications import (
    SOURCE_FUTURE_GPT_BRIDGE,
    SOURCE_INTERNAL_TEST,
    SOURCE_WORKSTATION_PWA,
)
from cofferdam.workstation.tasks.delegated import (
    KIND_CLARIFICATION_REQUESTED,
    ClarificationRequest,
    build_event,
)
from cofferdam.workstation.tasks.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_READY_FOR_FOLLOWUP,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
    WAITING_CLARIFICATION,
)
from cofferdam.workstation.tasks.store import TaskStore


# -- a conversational adapter double -----------------------------------------


class ConversationalAdapter(TaskAdapter):
    """An adapter that finishes a turn and keeps its session, like the SDK one.

    Deliberately not the Agent SDK adapter: this suite is about **Task Core's**
    follow-up contract, which has to hold for any adapter that can continue a
    session. The SDK adapter's own wiring is exercised in
    ``test_agent_sdk_adapter.py`` against the session and helper doubles.
    """

    adapter_id = "conversational"
    display_name = "Conversational adapter"
    description = "A test double that finishes a turn and holds its session."

    def __init__(
        self,
        *,
        adapter_id: str = "conversational",
        session_id: str = "sess-conv-1",
    ) -> None:
        self.adapter_id = adapter_id
        self._session_id = session_id
        #: Task ids whose session this adapter still holds.
        self.live: Dict[str, int] = {}
        #: Every follow-up delivered, as ``(task_id, text)``. A test reads it to
        #: prove a message reached one task's session and no other's.
        self.delivered: List[Any] = []
        self.refuse_followup: Optional[str] = None
        self.finish_next_inspect: Dict[str, str] = {}
        self.pending_question: Optional[str] = None
        self.cancelled: List[str] = []
        self.released: List[str] = []
        #: Clarification answers this adapter took, as ``(task_id, text)``.
        self.answers: List[Any] = []
        #: How many sessions this adapter has ever opened, per task. A second
        #: one for a task that already had a live session would mean a follow-up
        #: had started a new conversation.
        self.sessions_opened: Dict[str, int] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True,
            cancel=True,
            followup=True,
            final_result=True,
            structured_progress=True,
            clarifications=True,
        )

    def available(self) -> bool:
        return True

    def start(self, context: TaskContext) -> AdapterOutcome:
        self.live[context.task_id] = 1
        self.sessions_opened[context.task_id] = (
            self.sessions_opened.get(context.task_id, 0) + 1
        )
        return AdapterOutcome(events=(AdapterEvent(text="started"),))

    def session_available(self, task_id: str) -> bool:
        return task_id in self.live

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        if self.pending_question == context.task_id:
            self.pending_question = None
            return AdapterOutcome(
                events=(AdapterEvent(text="asking"),),
                requested_state=STATE_WAITING_FOR_USER,
                waiting_reason=WAITING_CLARIFICATION,
                clarification=ClarificationRequest.from_dict(
                    {
                        "category": "clarification",
                        "question": "Which branch?",
                        "answer_mode": "single_choice",
                        "options": [
                            {"option_id": "opt1", "label": "main"},
                            {"option_id": "opt2", "label": "develop"},
                        ],
                    }
                ),
                clarification_token="ask_conv_1",
                clarification_session_id=self._session_id,
                clarification_sequence=3,
            )
        result = self.finish_next_inspect.pop(context.task_id, None)
        if result is None:
            return AdapterOutcome()
        return self.complete_turn(context.task_id, result)

    def complete_turn(self, task_id: str, result: str) -> AdapterOutcome:
        """A turn that succeeded on a session this adapter is still holding."""
        turn = self.live.get(task_id, 1)
        return AdapterOutcome(
            events=(AdapterEvent(text="turn done"),),
            requested_state=STATE_READY_FOR_FOLLOWUP,
            final_result=result,
            provider_session_id=self._session_id,
            provider_turn_sequence=10 * turn,
            session_retained=True,
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        if self.refuse_followup is not None:
            raise AdapterRefusal(self.refuse_followup)
        if context.task_id not in self.live:
            raise AdapterRefusal("there is no running session for this task")
        self.live[context.task_id] += 1
        self.delivered.append((context.task_id, followup))
        return AdapterOutcome(
            events=(AdapterEvent(text="follow-up delivered"),),
            provider_session_id=self._session_id,
            session_retained=True,
        )

    def deliver_clarification_answer(
        self, context: TaskContext, token: str, answer: str
    ) -> bool:
        """The *other* continuation path, kept separate on purpose.

        Its own method taking its own arguments, so a test can prove that the
        answer route reaches this and the follow-up route reaches
        :meth:`send_followup`, and that neither can reach the other.
        """
        if token != "ask_conv_1" or context.task_id not in self.live:
            return False
        self.answers.append((context.task_id, answer))
        return True

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        self.cancelled.append(context.task_id)
        self.live.pop(context.task_id, None)
        return AdapterOutcome(
            requested_state=STATE_CANCELLED, provider_session_id=self._session_id
        )

    def release_session(self, task_id: str) -> None:
        self.released.append(task_id)
        self.live.pop(task_id, None)

    def lose_session(self, task_id: str) -> None:
        """The helper died. The task record is untouched, as it would be."""
        self.live.pop(task_id, None)


class FollowupTestCase(TaskTestCase):
    project_adapters = ("validation", "scripted", "conversational", "other")

    def setUp(self) -> None:
        super().setUp()
        self.agent = self.install_adapter(ConversationalAdapter())

    def answered_turn(self, result: str = "the first answer"):
        """A task whose first turn completed with the session still live."""
        row = self.create(adapter_id="conversational")
        self.agent.finish_next_inspect[row.task_id] = result
        row = self.service.refresh_task(row.task_id)
        self.assertEqual(row.state, STATE_READY_FOR_FOLLOWUP)
        return row

    def restart(self):
        """A daemon restart, done the way the daemon does it.

        Three things happen and all three matter. The store is reopened over the
        same file. The adapter is rebuilt **holding nothing** — which is the
        actual situation, because the helper processes died with the daemon and
        no dictionary of live sessions survives a process. And recovery runs,
        because the service does that once at start-up and the whole point of
        these tests is what it concludes.
        """
        service = super().restart()
        self.agent = self.install_adapter(ConversationalAdapter())
        self.service.recover_after_restart()
        return self.service


# -- 1. turns are durable, and one never overwrites another ------------------


class TurnDurabilityTests(FollowupTestCase):
    def test_a_first_turn_is_recorded_when_the_task_starts(self) -> None:
        row = self.create(adapter_id="conversational")
        turns = self.store.turns(row.task_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].turn_number, 1)
        self.assertEqual(turns[0].provider, "conversational")
        self.assertFalse(turns[0].completed)
        # The prompt opened it, so its source is the task's own origin rather
        # than a follow-up surface.
        self.assertEqual(turns[0].source, SOURCE_WORKSTATION_PWA)

    def test_a_completed_turn_records_its_result_and_provenance(self) -> None:
        row = self.answered_turn("kırk iki")
        turn = self.store.latest_completed_turn(row.task_id)
        self.assertEqual(turn.turn_number, 1)
        self.assertEqual(turn.outcome, STATE_COMPLETED)
        self.assertEqual(turn.result, "kırk iki")
        self.assertEqual(turn.provider_session_id, "sess-conv-1")
        self.assertEqual(turn.provider_turn_sequence, 10)
        self.assertIsNotNone(turn.completed_at)

    def test_a_second_turn_does_not_overwrite_the_first_turns_evidence(self) -> None:
        """The rule this whole table exists for.

        ``tasks.final_result`` is written with COALESCE and does move on. The
        turn rows do not, and the first turn's answer is still exactly what the
        person read before they typed the follow-up.
        """
        row = self.answered_turn("the first answer")
        self.service.send_followup(row.task_id, "and what about the second?")
        self.agent.finish_next_inspect[row.task_id] = "the second answer"
        self.service.refresh_task(row.task_id)

        turns = self.store.turns(row.task_id)
        self.assertEqual([t.turn_number for t in turns], [1, 2])
        self.assertEqual(turns[0].result, "the first answer")
        self.assertEqual(turns[1].result, "the second answer")
        self.assertTrue(all(t.completed for t in turns))
        # And the two are distinguishable by more than their text: the second
        # began no earlier than the first ended, and carries the provider's own
        # ordering alongside Cofferdam's.
        self.assertGreaterEqual(turns[1].started_at, turns[0].completed_at)
        self.assertLess(turns[0].provider_turn_sequence, turns[1].provider_turn_sequence)
        self.assertEqual(turns[1].source, SOURCE_WORKSTATION_PWA)

    def test_a_late_duplicate_cannot_rewrite_a_completed_turn(self) -> None:
        """The store's guard, exercised directly.

        Driven through the store because no adapter can produce this: the
        guarantee is about the row, not about today's callers.
        """
        row = self.answered_turn("the real answer")
        self.store._close_turn_locked  # the private guard under test exists
        with self.store._write() as connection:
            self.store._close_turn_locked(
                connection,
                row.task_id,
                store_module._TurnClose(
                    outcome=STATE_FAILED,
                    completed_at="2099-01-01T00:00:00Z",
                    failure_code="nonsense",
                    failure_summary="a late report",
                ),
            )
        turn = self.store.latest_completed_turn(row.task_id)
        self.assertEqual(turn.outcome, STATE_COMPLETED)
        self.assertEqual(turn.result, "the real answer")
        self.assertIsNone(turn.failure_code)

    def test_turn_numbers_are_allocated_without_gaps_or_collisions(self) -> None:
        row = self.answered_turn()
        for index in range(4):
            self.service.send_followup(row.task_id, "message " + str(index))
            self.agent.finish_next_inspect[row.task_id] = "answer " + str(index)
            self.service.refresh_task(row.task_id)
        numbers = [turn.turn_number for turn in self.store.turns(row.task_id)]
        self.assertEqual(numbers, [1, 2, 3, 4, 5])

    def test_a_turn_number_cannot_be_reused_even_under_a_direct_insert(self) -> None:
        """The primary key is the backstop behind the MAX+1 allocation."""
        row = self.answered_turn()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._write() as connection:
                connection.execute(
                    "INSERT INTO task_turns (task_id, turn_number, provider,"
                    " source, started_at) VALUES (?, 1, 'x', 'internal_test', 'now')",
                    (row.task_id,),
                )


# -- 2. concurrency ----------------------------------------------------------


class ConcurrencyTests(FollowupTestCase):
    def test_concurrent_follow_ups_produce_at_most_one_provider_turn(self) -> None:
        """Eight threads, one message.

        The refusals are as important as the acceptance: every thread either
        delivered the follow-up or was told why it could not, and none of them
        got a silent success.
        """
        row = self.answered_turn()
        accepted: List[Any] = []
        refused: List[Exception] = []
        barrier = threading.Barrier(8)

        def attempt(index: int) -> None:
            barrier.wait()
            try:
                self.service.send_followup(row.task_id, "same message")
                accepted.append(index)
            except task_errors.TaskError as refusal:
                refused.append(refusal)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(refused), 7)
        self.assertEqual(len(self.agent.delivered), 1)
        self.assertEqual(len(self.store.turns(row.task_id)), 2)

    def test_a_second_follow_up_while_one_is_running_is_refused(self) -> None:
        row = self.answered_turn()
        self.service.send_followup(row.task_id, "first")
        with self.assertRaises(task_errors.FollowupNotWaiting):
            # The task is `running` now, not `ready_for_followup`.
            self.service.send_followup(row.task_id, "second")
        self.assertEqual(len(self.agent.delivered), 1)


# -- 3. idempotency ----------------------------------------------------------


class IdempotencyTests(FollowupTestCase):
    def test_the_same_request_id_and_content_returns_the_original_outcome(self) -> None:
        row = self.answered_turn()
        first = self.service.send_followup(
            row.task_id, "devam et", client_request_id="req-1"
        )
        again = self.service.send_followup(
            row.task_id, "devam et", client_request_id="req-1"
        )
        self.assertEqual(first.task_id, again.task_id)
        self.assertEqual(len(self.agent.delivered), 1)
        self.assertEqual(len(self.store.turns(row.task_id)), 2)

    def test_the_same_request_id_with_different_content_conflicts(self) -> None:
        row = self.answered_turn()
        self.service.send_followup(row.task_id, "devam et", client_request_id="req-1")
        with self.assertRaises(task_errors.IdempotencyConflict):
            self.service.send_followup(
                row.task_id, "something else", client_request_id="req-1"
            )
        self.assertEqual(len(self.agent.delivered), 1)

    def test_a_retry_records_the_request_id_on_the_turn_it_opened(self) -> None:
        row = self.answered_turn()
        self.service.send_followup(row.task_id, "devam et", client_request_id="req-7")
        turn = self.store.turn_for_followup(row.task_id, "req-7")
        self.assertIsNotNone(turn)
        self.assertEqual(turn.turn_number, 2)
        self.assertIsNone(self.store.turn_for_followup(row.task_id, "req-never"))

    def test_the_unique_index_refuses_a_second_turn_for_one_request_id(self) -> None:
        """The database half of duplicate suppression, driven directly."""
        row = self.answered_turn()
        self.service.send_followup(row.task_id, "devam et", client_request_id="req-9")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._write() as connection:
                connection.execute(
                    "INSERT INTO task_turns (task_id, turn_number, provider,"
                    " source, started_at, followup_request_id)"
                    " VALUES (?, 99, 'x', 'internal_test', 'now', 'req-9')",
                    (row.task_id,),
                )


# -- 4. the follow-up state contract -----------------------------------------


class FollowupContractTests(FollowupTestCase):
    def test_a_finished_turn_with_a_live_session_accepts_one(self) -> None:
        row = self.answered_turn()
        updated = self.service.send_followup(row.task_id, "bir soru daha")
        self.assertEqual(updated.state, STATE_RUNNING)
        self.assertEqual(self.agent.delivered, [(row.task_id, "bir soru daha")])

    def test_a_pending_clarification_refuses_a_follow_up(self) -> None:
        """Refused, and the question is left open rather than superseded.

        The two channels stay separate: somebody who types a new instruction
        while the agent is blocked has not answered it, and delivering it as
        though they had would put words in their mouth.
        """
        row = self.create(adapter_id="conversational")
        self.agent.pending_question = row.task_id
        row = self.service.refresh_task(row.task_id)
        self.assertEqual(row.state, STATE_WAITING_FOR_USER)

        with self.assertRaises(task_errors.ClarificationPending):
            self.service.send_followup(row.task_id, "never mind that")

        self.assertEqual(len(self.service.pending_clarifications(row.task_id)), 1)
        self.assertEqual(self.store.get(row.task_id).state, STATE_WAITING_FOR_USER)
        self.assertEqual(self.agent.delivered, [])

    def test_a_cancelled_task_refuses_a_follow_up(self) -> None:
        row = self.answered_turn()
        self.service.cancel_task(row.task_id)
        with self.assertRaises(task_errors.TaskAlreadyFinished):
            self.service.send_followup(row.task_id, "one more thing")
        self.assertEqual(self.agent.delivered, [])

    def test_a_failed_task_refuses_a_follow_up(self) -> None:
        row = self.create(adapter_id="conversational")
        self.service._fail(row, "task_adapter_error", "it broke", None)
        with self.assertRaises(task_errors.TaskAlreadyFinished):
            self.service.send_followup(row.task_id, "one more thing")

    def test_a_lost_session_refuses_a_follow_up_truthfully(self) -> None:
        """The helper died. The task still says ``ready_for_followup``.

        That state is Cofferdam's memory of an observation, not the observation
        — so the adapter is asked, and the refusal names the session rather than
        the state.
        """
        row = self.answered_turn()
        self.agent.lose_session(row.task_id)
        with self.assertRaises(task_errors.SessionUnavailable):
            self.service.send_followup(row.task_id, "still there?")
        # And the task did not move on the strength of a failed attempt.
        self.assertEqual(self.store.get(row.task_id).state, STATE_READY_FOR_FOLLOWUP)

    def test_an_adapter_refusal_becomes_session_unavailable_not_a_failure(self) -> None:
        row = self.answered_turn()
        self.agent.refuse_followup = "the session did not take it"
        with self.assertRaises(task_errors.SessionUnavailable):
            self.service.send_followup(row.task_id, "hello")
        # Refused, not failed: the task is exactly where it was, and its result
        # is still readable.
        self.assertEqual(self.store.get(row.task_id).state, STATE_READY_FOR_FOLLOWUP)
        self.assertEqual(self.service.get_result(row.task_id).result, "the first answer")

    def test_an_adapter_without_the_capability_refuses(self) -> None:
        row = self.create(adapter_id="validation")
        with self.assertRaises(task_errors.TaskError):
            self.service.send_followup(row.task_id, "hello")

    def test_an_unknown_task_is_refused(self) -> None:
        with self.assertRaises(task_errors.TaskUnknown):
            self.service.send_followup("t_" + "0" * 32, "hello")

    def test_a_follow_up_reaches_only_the_task_it_names(self) -> None:
        first = self.answered_turn("first task answer")
        second = self.answered_turn("second task answer")
        self.service.send_followup(second.task_id, "only for the second")
        self.assertEqual(self.agent.delivered, [(second.task_id, "only for the second")])
        # The untouched task kept its state, its turn count and its result.
        self.assertEqual(self.store.get(first.task_id).state, STATE_READY_FOR_FOLLOWUP)
        self.assertEqual(len(self.store.turns(first.task_id)), 1)
        self.assertEqual(
            self.service.get_result(first.task_id).result, "first task answer"
        )

    def test_the_bridge_source_is_reserved_and_refused(self) -> None:
        row = self.answered_turn()
        with self.assertRaises(task_errors.FollowupInvalid):
            self.service.send_followup(
                row.task_id, "hello", source=SOURCE_FUTURE_GPT_BRIDGE
            )
        self.assertIn(SOURCE_FUTURE_GPT_BRIDGE, turns_module.FOLLOWUP_SOURCES)
        self.assertNotIn(
            SOURCE_FUTURE_GPT_BRIDGE, turns_module.ACCEPTED_FOLLOWUP_SOURCES
        )
        self.assertEqual(self.agent.delivered, [])

    def test_an_oversized_or_empty_follow_up_is_refused(self) -> None:
        row = self.answered_turn()
        for hostile in ("", "   ", "x" * 40000, "has\u0000a control character", 42):
            with self.assertRaises(task_errors.TaskError):
                self.service.send_followup(row.task_id, hostile)
        self.assertEqual(self.agent.delivered, [])


# -- 5. the result boundary --------------------------------------------------


class ResultTests(FollowupTestCase):
    def test_a_running_task_has_no_result_yet(self) -> None:
        row = self.create(adapter_id="conversational")
        with self.assertRaises(task_errors.ResultNotReady) as caught:
            self.service.get_result(row.task_id)
        self.assertEqual(caught.exception.code, "task_result_not_ready")
        # Not "no such task": the task exists and the detail says where it is.
        self.assertIn(STATE_RUNNING, caught.exception.detail)

    def test_an_unknown_task_is_not_found(self) -> None:
        with self.assertRaises(task_errors.TaskUnknown):
            self.service.get_result("t_" + "0" * 32)

    def test_a_ready_task_returns_its_turn_result_and_is_not_terminal(self) -> None:
        """The case the whole ``result_meaning`` field exists for."""
        row = self.answered_turn("kırk iki")
        result = self.service.get_result(row.task_id)
        self.assertEqual(result.result, "kırk iki")
        self.assertEqual(result.outcome, STATE_COMPLETED)
        self.assertTrue(result.succeeded)
        self.assertFalse(result.task_terminal)
        self.assertEqual(result.task_state, STATE_READY_FOR_FOLLOWUP)
        self.assertTrue(result.follow_up_available)
        self.assertEqual(result.turn_number, 1)
        self.assertEqual(result.turn_count, 1)

    def test_the_result_is_the_latest_completed_turn(self) -> None:
        row = self.answered_turn("the first answer")
        self.assertEqual(self.service.get_result(row.task_id).turn_number, 1)

        self.service.send_followup(row.task_id, "and again")
        self.agent.finish_next_inspect[row.task_id] = "the second answer"
        self.service.refresh_task(row.task_id)

        result = self.service.get_result(row.task_id)
        self.assertEqual(result.result, "the second answer")
        self.assertEqual(result.turn_number, 2)
        self.assertEqual(result.turn_count, 2)
        # The first turn is still on record even though it is not what a plain
        # `get_result` returns.
        self.assertEqual(self.store.turns(row.task_id)[0].result, "the first answer")

    def test_a_mid_turn_read_returns_the_previous_turn_and_offers_no_follow_up(
        self,
    ) -> None:
        """Turn two is running. Turn one's answer is still the honest result."""
        row = self.answered_turn("the first answer")
        self.service.send_followup(row.task_id, "and again")
        result = self.service.get_result(row.task_id)
        self.assertEqual(result.result, "the first answer")
        self.assertEqual(result.task_state, STATE_RUNNING)
        self.assertFalse(result.task_terminal)
        self.assertFalse(result.follow_up_available)

    def test_finishing_a_task_makes_the_same_result_terminal(self) -> None:
        row = self.answered_turn("kırk iki")
        self.service.finish_task(row.task_id)
        result = self.service.get_result(row.task_id)
        self.assertEqual(result.result, "kırk iki")
        self.assertTrue(result.task_terminal)
        self.assertEqual(result.task_state, STATE_COMPLETED)
        self.assertFalse(result.follow_up_available)

    def test_a_cancelled_task_with_no_answer_reports_cancellation(self) -> None:
        row = self.create(adapter_id="conversational")
        self.service.cancel_task(row.task_id)
        result = self.service.get_result(row.task_id)
        self.assertEqual(result.outcome, STATE_CANCELLED)
        self.assertTrue(result.task_terminal)
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.result)
        self.assertIsNotNone(result.completed_at)
        self.assertFalse(result.follow_up_available)

    def test_a_cancelled_task_keeps_the_answer_an_earlier_turn_produced(self) -> None:
        row = self.answered_turn("the first answer")
        self.service.cancel_task(row.task_id)
        result = self.service.get_result(row.task_id)
        # The turn succeeded and the task was cancelled. Both are true, and the
        # response says both rather than picking one.
        self.assertEqual(result.result, "the first answer")
        self.assertEqual(result.task_state, STATE_CANCELLED)
        self.assertTrue(result.task_terminal)
        self.assertFalse(result.follow_up_available)

    def test_a_failed_task_reports_a_bounded_category_and_summary(self) -> None:
        row = self.create(adapter_id="conversational")
        self.service._fail(row, "task_adapter_error", "the adapter stopped", "KeyError")
        result = self.service.get_result(row.task_id)
        self.assertEqual(result.outcome, STATE_FAILED)
        self.assertEqual(result.failure_code, "task_adapter_error")
        self.assertEqual(result.failure_summary, "the adapter stopped")
        self.assertIsNone(result.result)

    def test_an_interrupted_task_is_distinguishable_from_a_failed_one(self) -> None:
        row = self.create(adapter_id="conversational")
        service = self.restart()
        result = service.get_result(row.task_id)
        self.assertEqual(result.outcome, STATE_INTERRUPTED)
        self.assertTrue(result.task_terminal)
        self.assertNotEqual(result.outcome, STATE_FAILED)
        self.assertFalse(result.follow_up_available)

    def test_the_published_result_carries_nothing_it_should_not(self) -> None:
        row = self.answered_turn("kırk iki")
        payload = self.service.get_result(row.task_id).to_dict()
        allowed = {
            "version",
            "task_id",
            "task_state",
            "task_terminal",
            "outcome",
            "succeeded",
            "completed_at",
            "provider",
            "provider_session_id",
            "turn_number",
            "provider_turn_sequence",
            "turn_count",
            "result",
            "failure_code",
            "failure_summary",
            "follow_up_available",
            "evidence_source",
            "result_meaning",
        }
        self.assertEqual(set(payload), allowed)
        blob = json.dumps(payload)
        for forbidden in (
            "Traceback",
            "thinking",
            "tool_input",
            "transcript",
            "Bearer",
            "ANTHROPIC",
            "cwd",
            "argv",
            self.config.home.as_posix(),
        ):
            self.assertNotIn(forbidden, blob)

    def test_result_text_is_bounded(self) -> None:
        row = self.create(adapter_id="conversational")
        self.agent.finish_next_inspect[row.task_id] = "y" * 40000
        self.service.refresh_task(row.task_id)
        result = self.service.get_result(row.task_id)
        self.assertLessEqual(len(result.result), turns_module.MAX_TURN_RESULT_CHARS)

    def test_the_result_states_its_own_meaning(self) -> None:
        row = self.answered_turn()
        payload = self.service.get_result(row.task_id).to_dict()
        self.assertIn("latest completed turn", payload["result_meaning"])
        self.assertEqual(payload["evidence_source"], "adapter_reported")

    def test_reading_a_result_changes_nothing(self) -> None:
        row = self.answered_turn()
        before = self.store.get(row.task_id)
        for _ in range(3):
            self.service.get_result(row.task_id)
        after = self.store.get(row.task_id)
        self.assertEqual(before.lifecycle_revision, after.lifecycle_revision)
        self.assertEqual(before.event_cursor, after.event_cursor)
        self.assertEqual(before.updated_at, after.updated_at)


# -- 6. restart and interruption ---------------------------------------------


class RestartTests(FollowupTestCase):
    def test_a_restart_refuses_a_follow_up_and_keeps_the_earlier_result(self) -> None:
        """The honest half of restart, and the whole reason this is not resumed.

        The helper process died with the daemon. The conversation it held is
        gone, and nothing anybody types now could reach it — but the answer it
        produced was written down, and that is still true.
        """
        row = self.answered_turn("the answer from before the restart")
        service = self.restart()

        settled = service.store.get(row.task_id)
        self.assertEqual(settled.state, STATE_INTERRUPTED)

        with self.assertRaises(task_errors.TaskAlreadyFinished):
            service.send_followup(row.task_id, "are you still there?")

        result = service.get_result(row.task_id)
        self.assertEqual(result.result, "the answer from before the restart")
        self.assertEqual(result.task_state, STATE_INTERRUPTED)
        self.assertFalse(result.follow_up_available)

    def test_a_restart_closes_the_turn_that_was_running_as_interrupted(self) -> None:
        row = self.answered_turn("first")
        self.service.send_followup(row.task_id, "second question")
        service = self.restart()
        turns = service.store.turns(row.task_id)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].outcome, STATE_COMPLETED)
        self.assertEqual(turns[0].result, "first")
        self.assertEqual(turns[1].outcome, STATE_INTERRUPTED)
        self.assertIsNone(turns[1].result)
        # The completed turn is still what a result read returns.
        self.assertEqual(service.get_result(row.task_id).result, "first")

    def test_no_recovery_is_attempted_by_session_id(self) -> None:
        """Nothing reattaches, and the record says so rather than implying it."""
        row = self.answered_turn()
        session_id = self.store.latest_completed_turn(row.task_id).provider_session_id
        self.assertEqual(session_id, "sess-conv-1")
        service = self.restart()
        adapter = service.adapters.get("conversational")
        # A rebuilt adapter holds nothing, whatever any stored id says.
        self.assertFalse(adapter.session_available(row.task_id))
        self.assertFalse(adapter.capabilities().recover_after_restart)


# -- 7. cancellation ---------------------------------------------------------


class CancellationTests(FollowupTestCase):
    def test_cancelling_mid_follow_up_reaches_only_the_owning_session(self) -> None:
        other = self.answered_turn("untouched")
        row = self.answered_turn("first")
        self.service.send_followup(row.task_id, "second question")
        self.service.cancel_task(row.task_id)

        self.assertEqual(self.agent.cancelled, [row.task_id])
        self.assertEqual(self.store.get(row.task_id).state, STATE_CANCELLED)
        self.assertEqual(self.store.get(other.task_id).state, STATE_READY_FOR_FOLLOWUP)
        self.assertEqual(self.service.get_result(other.task_id).result, "untouched")

    def test_a_repeated_cancel_is_truthful(self) -> None:
        row = self.answered_turn()
        self.service.cancel_task(row.task_id)
        with self.assertRaises(task_errors.TaskAlreadyFinished):
            self.service.cancel_task(row.task_id)

    def test_a_late_result_cannot_overwrite_a_cancellation(self) -> None:
        row = self.answered_turn("first")
        self.service.send_followup(row.task_id, "second question")
        self.service.cancel_task(row.task_id)

        # The provider's answer to turn two arrives after the cancel.
        self.agent.finish_next_inspect[row.task_id] = "too late"
        self.service.refresh_task(row.task_id)

        self.assertEqual(self.store.get(row.task_id).state, STATE_CANCELLED)
        turns = self.store.turns(row.task_id)
        self.assertEqual(turns[1].outcome, STATE_CANCELLED)
        self.assertIsNone(turns[1].result)
        result = self.service.get_result(row.task_id)
        self.assertEqual(result.result, "first")
        self.assertNotEqual(result.result, "too late")

    def test_a_cancel_after_a_follow_up_still_reports_cancelled(self) -> None:
        row = self.answered_turn()
        self.service.send_followup(row.task_id, "keep going")
        self.service.cancel_task(row.task_id)
        self.assertTrue(self.service.get_result(row.task_id).task_terminal)
        self.assertEqual(
            self.service.get_result(row.task_id).task_state, STATE_CANCELLED
        )


# -- 8. provenance -----------------------------------------------------------


class ProvenanceTests(FollowupTestCase):
    def audit_for(self, operation: str) -> List[Dict[str, Any]]:
        return [entry for entry in self.audit if entry["operation"] == operation]

    def test_an_accepted_follow_up_is_audited_without_its_content(self) -> None:
        row = self.answered_turn()
        secret = "MY-SECRET-FOLLOWUP-TEXT"
        self.service.send_followup(row.task_id, secret)

        entries = self.audit_for("task_followup")
        self.assertEqual([entry["result"] for entry in entries], ["accepted"])
        self.assertEqual(entries[0]["task_id"], row.task_id)
        self.assertEqual(entries[0]["adapter_id"], "conversational")
        self.assertEqual(entries[0]["project_id"], PROJECT_ID)
        self.assertNotIn(secret, json.dumps(self.audit))

    def test_a_rejected_follow_up_is_audited_as_rejected(self) -> None:
        row = self.answered_turn()
        self.agent.lose_session(row.task_id)
        with self.assertRaises(task_errors.SessionUnavailable):
            self.service.send_followup(row.task_id, "hello")
        self.assertEqual(
            [entry["result"] for entry in self.audit_for("task_followup")], ["rejected"]
        )

    def test_the_history_records_shape_and_never_the_message(self) -> None:
        row = self.answered_turn()
        secret = "MY-SECRET-FOLLOWUP-TEXT"
        self.service.send_followup(row.task_id, secret)
        events = self.store.events(row.task_id, after=0, limit=200)
        received = [e for e in events if e.event_type == "followup_received"]
        self.assertEqual(len(received), 1)
        self.assertIn(str(len(secret)), received[0].text)
        self.assertNotIn(secret, json.dumps([e.to_dict() for e in events]))

    def test_a_turn_records_its_source_actor_and_session(self) -> None:
        row = self.answered_turn()
        self.service.send_followup(row.task_id, "devam", client_request_id="req-3")
        turn = self.store.turns(row.task_id)[1]
        self.assertEqual(turn.source, SOURCE_WORKSTATION_PWA)
        self.assertEqual(turn.followup_request_id, "req-3")
        self.assertEqual(turn.provider, "conversational")
        self.assertEqual(turn.provider_session_id, "sess-conv-1")
        self.assertIsNotNone(turn.started_at)

    def test_a_turn_row_holds_no_message_text(self) -> None:
        """The follow-up itself is not copied into the turn record.

        A turn keeps what the *provider* produced, which somebody asked to be
        shown. What they typed lives on the task, once.
        """
        row = self.answered_turn()
        secret = "MY-SECRET-FOLLOWUP-TEXT"
        self.service.send_followup(row.task_id, secret)
        self.agent.finish_next_inspect[row.task_id] = "an answer"
        self.service.refresh_task(row.task_id)
        blob = json.dumps([turn.to_dict() for turn in self.store.turns(row.task_id)])
        self.assertNotIn(secret, blob)

    def test_the_source_vocabulary_is_shared_with_clarification_answers(self) -> None:
        """One vocabulary, two uses. Two lists that must agree would not."""
        from cofferdam.workstation.tasks import clarifications as clar

        self.assertEqual(
            set(turns_module.FOLLOWUP_SOURCES), set(clar.ANSWER_SOURCES)
        )
        self.assertEqual(
            turns_module.ACCEPTED_FOLLOWUP_SOURCES, clar.ACCEPTED_ANSWER_SOURCES
        )

    def test_every_origin_maps_to_a_source_without_a_default(self) -> None:
        from cofferdam.workstation.tasks.models import ORIGINS

        for origin in ORIGINS:
            self.assertIn(origin, turns_module.SOURCE_FOR_ORIGIN)
        # And the bridge is not something an unknown origin can fall into.
        self.assertEqual(
            turns_module.source_for_origin("something_new"), SOURCE_INTERNAL_TEST
        )


# -- 9. separation of the three concepts -------------------------------------


class SeparationTests(FollowupTestCase):
    def test_a_follow_up_is_not_a_clarification_answer(self) -> None:
        """Two operations, two methods, two code paths, one refusal each way."""
        row = self.create(adapter_id="conversational")
        self.agent.pending_question = row.task_id
        row = self.service.refresh_task(row.task_id)
        question_id = self.service.pending_clarifications(row.task_id)[0].question_id

        # The follow-up route will not answer the question.
        with self.assertRaises(task_errors.ClarificationPending):
            self.service.send_followup(row.task_id, "opt1")

        # And the answer route is the one that works.
        self.service.answer_clarification(
            row.task_id, question_id, {"option_ids": ["opt1"]}
        )
        self.assertEqual(self.store.get(row.task_id).state, STATE_RUNNING)
        # The answer went to the answer channel and the follow-up channel saw
        # nothing. Two lists, and the emptiness of the second is the assertion.
        self.assertEqual(len(self.agent.answers), 1)
        self.assertEqual(self.agent.delivered, [])

    def test_neither_route_is_a_tool_approval(self) -> None:
        """There is no method, field or code that could grant a tool.

        Asserted as an absence, because that is what the guarantee is.
        """
        self.assertFalse(self.agent.capabilities().approvals)
        for forbidden in ("approve_tool", "deliver_tool_approval", "grant"):
            self.assertFalse(hasattr(self.service, forbidden))
        source = (
            __import__("inspect")
            .getsource(type(self.service))
            .lower()
        )
        self.assertNotIn("permissionresultallow", source)

    def test_the_three_operations_have_three_distinct_errors(self) -> None:
        codes = {
            task_errors.CODE_CLARIFICATION_PENDING,
            task_errors.CODE_FOLLOWUP_NOT_WAITING,
            task_errors.CODE_SESSION_UNAVAILABLE,
        }
        self.assertEqual(len(codes), 3)
        self.assertNotIn("approval", " ".join(codes))


# -- 10. the migration -------------------------------------------------------


class MigrationTests(TaskTestCase):
    def test_the_schema_version_is_three(self) -> None:
        self.assertEqual(store_module.SCHEMA_VERSION, 3)

    def test_a_version_two_database_gains_the_turn_table_and_keeps_its_rows(
        self,
    ) -> None:
        """A real version-2 database, upgraded by opening it.

        Written by dropping the new table and setting the version back, rather
        than mocked, because the thing under test is what SQLite does when the
        schema script runs against an existing file — and what the existing rows
        look like afterwards.
        """
        row = self.create()
        self.store.close()

        connection = sqlite3.connect(str(self.store.path))
        try:
            connection.execute("DROP TABLE task_turns")
            connection.execute(
                "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'"
            )
            connection.commit()
            tables = {
                name for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertNotIn("task_turns", tables)
        finally:
            connection.close()

        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)

        # The task written before the upgrade is untouched.
        preserved = reopened.get(row.task_id)
        self.assertEqual(preserved.task_id, row.task_id)
        self.assertEqual(preserved.prompt, TURKISH_PROMPT)
        self.assertEqual(preserved.state, row.state)
        self.assertTrue(reopened.events(row.task_id, after=0, limit=10))

        # The new table exists and is empty, and the version moved.
        self.assertEqual(reopened.turns(row.task_id), [])
        connection = sqlite3.connect(str(reopened.path))
        try:
            value = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(int(value), 3)

    def test_a_task_predating_turns_still_reports_its_terminal_outcome(self) -> None:
        """No turn row is an ordinary answer, not a missing record.

        A task written by a version-2 build has no turns and can still be
        terminal. Its outcome comes from the task row, which is where it always
        was — so an upgraded database answers for its old tasks rather than
        erroring on them.
        """
        row = self.create()
        row = self.service.refresh_task(row.task_id)
        with self.store._write() as connection:
            connection.execute(
                "DELETE FROM task_turns WHERE task_id = ?", (row.task_id,)
            )
        self.assertIsNone(self.store.latest_completed_turn(row.task_id))

        if row.state in {STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED}:
            result = self.service.get_result(row.task_id)
            self.assertEqual(result.outcome, row.state)
            self.assertEqual(result.turn_count, 0)
            self.assertIsNone(result.result)
        else:  # pragma: no cover - depends on the validation adapter's script
            with self.assertRaises(task_errors.ResultNotReady):
                self.service.get_result(row.task_id)

    def test_the_upgrade_writes_no_rows(self) -> None:
        """Additive means additive: nothing is back-filled or invented."""
        row = self.create()
        with self.store._write() as connection:
            connection.execute(
                "DELETE FROM task_turns WHERE task_id = ?", (row.task_id,)
            )
        self.store.close()
        reopened = TaskStore(self.config)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.turn_count(row.task_id), 0)


# -- 11. the routes ----------------------------------------------------------

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the extras are absent
    TestClient = None

API_TOKEN = "test-device-token-not-a-real-credential"


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class FollowupApiTests(FollowupTestCase):
    """The two routes over HTTP, against the real service and store."""

    def setUp(self) -> None:
        super().setUp()
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.service import create_app

        self.app = create_app(
            config=self.config,
            token=API_TOKEN,
            adapter=StubAdapter(self.config),
            tasks=self.service,
        )
        self.client = TestClient(self.app)
        self.auth = {"Authorization": "Bearer " + API_TOKEN}

    def result_path(self, task_id: str) -> str:
        return "/api/tasks/" + task_id + "/result"

    def followup_path(self, task_id: str) -> str:
        return "/api/tasks/" + task_id + "/followups"

    # -- authentication ------------------------------------------------------

    def test_both_routes_require_the_device_token(self) -> None:
        row = self.answered_turn()
        self.assertEqual(self.client.get(self.result_path(row.task_id)).status_code, 401)
        self.assertEqual(
            self.client.post(self.followup_path(row.task_id), json={}).status_code, 401
        )

    def test_a_wrong_token_is_refused(self) -> None:
        row = self.answered_turn()
        response = self.client.get(
            self.result_path(row.task_id), headers={"Authorization": "Bearer wrong"}
        )
        self.assertEqual(response.status_code, 401)

    # -- results -------------------------------------------------------------

    def test_the_result_route_returns_the_normalized_shape(self) -> None:
        row = self.answered_turn("kırk iki")
        response = self.client.get(self.result_path(row.task_id), headers=self.auth)
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["result"], "kırk iki")
        self.assertEqual(result["turn_number"], 1)
        self.assertFalse(result["task_terminal"])
        self.assertTrue(result["follow_up_available"])

    def test_the_result_response_is_no_store(self) -> None:
        row = self.answered_turn()
        response = self.client.get(self.result_path(row.task_id), headers=self.auth)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_a_not_ready_result_is_a_conflict_not_a_not_found(self) -> None:
        row = self.create(adapter_id="conversational")
        response = self.client.get(self.result_path(row.task_id), headers=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "task_result_not_ready")

    def test_an_unknown_task_result_is_not_found(self) -> None:
        response = self.client.get(
            self.result_path("t_" + "0" * 32), headers=self.auth
        )
        self.assertEqual(response.status_code, 404)

    def test_the_result_route_takes_no_query_flags(self) -> None:
        row = self.answered_turn("kırk iki")
        response = self.client.get(
            self.result_path(row.task_id) + "?turn=1&raw=true&session_id=x",
            headers=self.auth,
        )
        # Accepted and ignored: there is no parameter to bind them to, so they
        # cannot change what comes back.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["result"], "kırk iki")

    # -- follow-ups ----------------------------------------------------------

    def test_a_follow_up_is_accepted_over_http(self) -> None:
        row = self.answered_turn()
        response = self.client.post(
            self.followup_path(row.task_id),
            headers=self.auth,
            json={"followup": "devam et", "client_request_id": "req-1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["state"], STATE_RUNNING)
        self.assertEqual(self.agent.delivered, [(row.task_id, "devam et")])

    def test_the_body_vocabulary_is_closed(self) -> None:
        """Absent, not validated. The refusal names the field it did not want."""
        row = self.answered_turn()
        for forbidden in (
            "provider_session_id",
            "session_id",
            "model",
            "tools",
            "cwd",
            "path",
            "executable",
            "argv",
            "env",
            "permission_mode",
            "flags",
            "resume",
            "max_turns",
            "source",
        ):
            response = self.client.post(
                self.followup_path(row.task_id),
                headers=self.auth,
                json={"followup": "hello", forbidden: "anything"},
            )
            self.assertEqual(response.status_code, 422, forbidden)
            self.assertIn("unexpected field", response.json()["error"]["message"])
        self.assertEqual(self.agent.delivered, [])

    def test_an_approval_shaped_body_is_refused_by_the_follow_up_route(self) -> None:
        row = self.answered_turn()
        for forbidden in ("tool_name", "decision", "allow", "approval_id", "behavior"):
            response = self.client.post(
                self.followup_path(row.task_id),
                headers=self.auth,
                json={"followup": "yes", forbidden: "Bash"},
            )
            self.assertEqual(response.status_code, 422, forbidden)
        self.assertEqual(self.agent.delivered, [])

    def test_a_clarification_shaped_body_is_refused_by_the_follow_up_route(
        self,
    ) -> None:
        row = self.answered_turn()
        for forbidden in ("question_id", "option_ids", "answer"):
            response = self.client.post(
                self.followup_path(row.task_id),
                headers=self.auth,
                json={"followup": "yes", forbidden: "opt1"},
            )
            self.assertEqual(response.status_code, 422, forbidden)

    def test_a_follow_up_body_is_refused_by_the_clarification_route(self) -> None:
        row = self.create(adapter_id="conversational")
        self.agent.pending_question = row.task_id
        self.service.refresh_task(row.task_id)
        question_id = self.service.pending_clarifications(row.task_id)[0].question_id
        response = self.client.post(
            "/api/tasks/" + row.task_id + "/clarifications/" + question_id + "/answer",
            headers=self.auth,
            json={"followup": "a new instruction"},
        )
        self.assertEqual(response.status_code, 422)

    def test_a_pending_question_refuses_a_follow_up_with_its_own_code(self) -> None:
        row = self.create(adapter_id="conversational")
        self.agent.pending_question = row.task_id
        self.service.refresh_task(row.task_id)
        response = self.client.post(
            self.followup_path(row.task_id),
            headers=self.auth,
            json={"followup": "never mind"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "task_clarification_pending"
        )

    def test_a_lost_session_refuses_with_its_own_code(self) -> None:
        row = self.answered_turn()
        self.agent.lose_session(row.task_id)
        response = self.client.post(
            self.followup_path(row.task_id),
            headers=self.auth,
            json={"followup": "still there?"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "task_session_unavailable")

    def test_a_conflicting_request_id_is_a_conflict(self) -> None:
        row = self.answered_turn()
        self.client.post(
            self.followup_path(row.task_id),
            headers=self.auth,
            json={"followup": "one", "client_request_id": "req-1"},
        )
        response = self.client.post(
            self.followup_path(row.task_id),
            headers=self.auth,
            json={"followup": "two", "client_request_id": "req-1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "task_idempotency_conflict")

    def test_no_route_exposes_turns_or_a_bridge_surface(self) -> None:
        """What is not routed. The bridge is M2I.5 and does not exist."""
        paths = {getattr(route, "path", "") for route in self.app.routes}
        self.assertIn("/api/tasks/{task_id}/result", paths)
        self.assertIn("/api/tasks/{task_id}/followups", paths)
        for absent in (
            "/api/tasks/{task_id}/turns",
            "/api/tasks/{task_id}/transcript",
            "/api/tasks/{task_id}/approvals",
            "/api/tasks/{task_id}/answer",
            "/api/gpt/tasks/{task_id}/result",
        ):
            self.assertNotIn(absent, paths)


if __name__ == "__main__":
    unittest.main()
