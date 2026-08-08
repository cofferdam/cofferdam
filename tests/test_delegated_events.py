"""The provider-neutral delegated-session event model.

Standard library only, and deliberately so: this module is the part of M2I that
must be correct whether or not the Agent SDK is installed, and a test that
skipped on the stdlib-only CI job would leave the safety boundary it checks
untested on the job that exists to catch exactly that.

The boundary being checked is the one in the module's own docstring: a
clarification and a tool approval are different acts, a client allowed to answer
the first must not thereby answer the second, and the two must be impossible to
confuse *in storage* rather than by convention at each layer above.
"""

from __future__ import annotations

import unittest

from cofferdam.workstation.tasks import delegated
from cofferdam.workstation.tasks.delegated import (
    CATEGORY_CLARIFICATION,
    CATEGORY_TOOL_APPROVAL,
    DELEGATED_KINDS,
    KIND_ACTIVITY,
    KIND_CANCELLED,
    KIND_CLARIFICATION_REQUESTED,
    KIND_OUTPUT,
    KIND_PROVIDER_FAILED,
    KIND_SESSION_STARTED,
    KIND_SUCCEEDED,
    KIND_TOOL_APPROVAL_REQUESTED,
    MAX_OPTIONS,
    MAX_QUESTION_CHARS,
    TERMINAL_KINDS,
    ClarificationRequest,
    DelegatedEvent,
    DelegatedEventInvalid,
    DelegatedEventLog,
    ToolApprovalRequest,
    build_event,
    projection,
    result_from_event,
    safe_text,
)
from cofferdam.workstation.tasks.models import (
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_PROGRESS,
    EVENT_WAITING_FOR_USER,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    WAITING_APPROVAL,
    WAITING_CLARIFICATION,
)

NOW = "2026-08-09T00:00:00Z"


def event(kind: str, sequence: int = 1, **fields):
    return build_event(
        kind=kind,
        provider="claude-agent-sdk",
        provider_sequence=sequence,
        observed_at=NOW,
        **fields,
    )


def clarification(question: str = "Which branch?", options=()) -> ClarificationRequest:
    return ClarificationRequest.from_dict(
        {
            "category": CATEGORY_CLARIFICATION,
            "question": question,
            "options": list(options),
        }
    )


def approval(tool: str = "Bash") -> ToolApprovalRequest:
    return ToolApprovalRequest.from_dict(
        {"category": CATEGORY_TOOL_APPROVAL, "tool_name": tool}
    )


class VocabularyTests(unittest.TestCase):
    def test_the_kind_vocabulary_is_closed(self) -> None:
        with self.assertRaises(DelegatedEventInvalid):
            event("something_new")

    def test_every_terminal_kind_maps_to_a_terminal_task_state(self) -> None:
        """A terminal kind with no state would be a session that ends nowhere."""
        for kind in TERMINAL_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, delegated.TERMINAL_STATE_FOR_KIND)
        self.assertEqual(
            set(delegated.TERMINAL_STATE_FOR_KIND), set(TERMINAL_KINDS)
        )

    def test_only_the_two_request_kinds_produce_a_waiting_reason(self) -> None:
        for kind in DELEGATED_KINDS:
            if kind in (KIND_CLARIFICATION_REQUESTED, KIND_TOOL_APPROVAL_REQUESTED):
                continue
            with self.subTest(kind=kind):
                self.assertIsNone(event(kind).waiting_reason)


class SeparationTests(unittest.TestCase):
    """The point of the module. Both directions, several ways each."""

    def test_a_clarification_carries_a_clarification_waiting_reason(self) -> None:
        built = event(KIND_CLARIFICATION_REQUESTED, clarification=clarification())
        self.assertEqual(built.waiting_reason, WAITING_CLARIFICATION)

    def test_a_tool_approval_carries_an_approval_waiting_reason(self) -> None:
        built = event(KIND_TOOL_APPROVAL_REQUESTED, approval=approval())
        self.assertEqual(built.waiting_reason, WAITING_APPROVAL)

    def test_a_clarification_cannot_deserialize_as_a_tool_approval(self) -> None:
        stored = clarification().to_dict()
        with self.assertRaises(DelegatedEventInvalid):
            ToolApprovalRequest.from_dict(stored)

    def test_a_tool_approval_cannot_deserialize_as_a_clarification(self) -> None:
        stored = approval().to_dict()
        with self.assertRaises(DelegatedEventInvalid):
            ClarificationRequest.from_dict(stored)

    def test_a_payload_that_lies_about_its_category_is_still_refused(self) -> None:
        """The category alone is not enough, and must not be.

        A record that claimed ``clarification`` while carrying a tool name is the
        exact shape a bug would produce, so the field check runs regardless of
        what the discriminator says.
        """
        smuggled = {
            "category": CATEGORY_CLARIFICATION,
            "question": "May I run this?",
            "tool_name": "Bash",
        }
        with self.assertRaises(DelegatedEventInvalid):
            ClarificationRequest.from_dict(smuggled)

        reverse = {
            "category": CATEGORY_TOOL_APPROVAL,
            "tool_name": "Bash",
            "question": "Which branch?",
        }
        with self.assertRaises(DelegatedEventInvalid):
            ToolApprovalRequest.from_dict(reverse)

    def test_an_event_cannot_hold_both(self) -> None:
        with self.assertRaises(DelegatedEventInvalid):
            DelegatedEvent(
                kind=KIND_CLARIFICATION_REQUESTED,
                provider="p",
                provider_sequence=1,
                observed_at=NOW,
                clarification=clarification(),
                approval=approval(),
            )

    def test_a_request_cannot_be_attached_to_an_unrelated_kind(self) -> None:
        for kind in (KIND_ACTIVITY, KIND_OUTPUT, KIND_SUCCEEDED):
            with self.subTest(kind=kind):
                with self.assertRaises(DelegatedEventInvalid):
                    event(kind, clarification=clarification())
                with self.assertRaises(DelegatedEventInvalid):
                    event(kind, approval=approval())

    def test_the_two_kinds_project_to_visibly_different_history_entries(self) -> None:
        _, clarify_text, clarify_detail = projection(
            event(KIND_CLARIFICATION_REQUESTED, clarification=clarification())
        )
        _, approve_text, approve_detail = projection(
            event(KIND_TOOL_APPROVAL_REQUESTED, approval=approval())
        )
        self.assertEqual(clarify_detail, "clarification")
        self.assertEqual(approve_detail, "tool approval")
        self.assertNotEqual(clarify_text, approve_text)
        # The approval's sentence has to say where the decision belongs, because
        # this is the one a person must not answer from a phone.
        self.assertIn("workstation", approve_text)

    def test_a_tool_approval_has_nowhere_to_put_a_question(self) -> None:
        """Structural, not validated: the field does not exist."""
        self.assertFalse(hasattr(approval(), "question"))
        self.assertFalse(hasattr(approval(), "options"))
        self.assertNotIn("question", approval().to_dict())

    def test_a_clarification_has_nowhere_to_put_a_tool(self) -> None:
        self.assertFalse(hasattr(clarification(), "tool_name"))
        self.assertNotIn("tool_name", clarification().to_dict())


class BoundsTests(unittest.TestCase):
    def test_an_oversized_question_is_truncated_not_refused(self) -> None:
        built = clarification("q" * (MAX_QUESTION_CHARS * 3))
        self.assertLessEqual(len(built.question), MAX_QUESTION_CHARS)

    def test_a_clarification_with_no_question_is_refused(self) -> None:
        for empty in ("", "   ", None, 17):
            with self.subTest(value=empty):
                with self.assertRaises(DelegatedEventInvalid):
                    ClarificationRequest.from_dict(
                        {"category": CATEGORY_CLARIFICATION, "question": empty}
                    )

    def test_an_approval_with_no_usable_tool_name_is_refused(self) -> None:
        for bad in ("", "<img onerror=x>", "a" * 100, None, 3):
            with self.subTest(value=bad):
                with self.assertRaises(DelegatedEventInvalid):
                    ToolApprovalRequest.from_dict(
                        {"category": CATEGORY_TOOL_APPROVAL, "tool_name": bad}
                    )

    def test_option_count_and_length_are_bounded(self) -> None:
        built = clarification(
            options=[{"label": "o" * 500, "value": "v" * 500}] * (MAX_OPTIONS * 4)
        )
        self.assertLessEqual(len(built.options), MAX_OPTIONS)
        for option in built.options:
            self.assertLessEqual(len(option.label), delegated.MAX_OPTION_LABEL_CHARS)
            self.assertLessEqual(len(option.value), delegated.MAX_OPTION_VALUE_CHARS)

    def test_unusable_options_are_dropped_and_the_question_survives(self) -> None:
        built = clarification(options=[{"label": "  "}, 5, {"label": "keep"}])
        self.assertEqual([option.label for option in built.options], ["keep"])

    def test_escape_sequences_and_bidi_overrides_do_not_survive(self) -> None:
        hostile = "\x1b]0;title\x07\x1b[31mred\x1b[0m‮gnitirw"
        cleaned = safe_text(hostile, 200)
        self.assertNotIn("\x1b", cleaned)
        self.assertNotIn("0;title", cleaned)
        self.assertNotIn("‮", cleaned)
        self.assertIn("red", cleaned)

    def test_turkish_text_is_ordinary_text(self) -> None:
        """Not a special case, and it must never become one."""
        self.assertEqual(safe_text("Türkçe karakterler", 100), "Türkçe karakterler")

    def test_an_activity_summary_is_much_shorter_than_a_result(self) -> None:
        long = "x" * 40000
        self.assertLessEqual(
            len(event(KIND_ACTIVITY, text=long).text),
            delegated.MAX_ACTIVITY_SUMMARY_CHARS,
        )
        self.assertLessEqual(
            len(event(KIND_SUCCEEDED, text=long, result=long).result),
            delegated.MAX_RESULT_TEXT_CHARS,
        )

    def test_only_a_success_carries_a_result_and_only_a_failure_a_code(self) -> None:
        with self.assertRaises(DelegatedEventInvalid):
            DelegatedEvent(
                kind=KIND_ACTIVITY,
                provider="p",
                provider_sequence=1,
                observed_at=NOW,
                result="sneaky",
            )
        with self.assertRaises(DelegatedEventInvalid):
            DelegatedEvent(
                kind=KIND_ACTIVITY,
                provider="p",
                provider_sequence=1,
                observed_at=NOW,
                failure_code="nope",
            )

    def test_a_nonsense_failure_code_becomes_the_generic_one(self) -> None:
        built = event(KIND_PROVIDER_FAILED, failure_code="Not A Code!", text="broke")
        self.assertEqual(built.failure_code, "provider_error")

    def test_a_malformed_session_id_is_dropped_rather_than_stored(self) -> None:
        built = event(KIND_ACTIVITY, provider_session_id="bad id\nwith newline")
        self.assertIsNone(built.provider_session_id)


class NoPayloadTests(unittest.TestCase):
    def test_no_class_here_can_hold_a_provider_object(self) -> None:
        """Checked by name, because the guarantee is the absence of a field."""
        forbidden = {"raw", "payload", "data", "message", "response", "blocks"}
        for cls in (
            DelegatedEvent,
            ClarificationRequest,
            ToolApprovalRequest,
            delegated.ClarificationOption,
            delegated.DelegatedResult,
        ):
            with self.subTest(cls=cls.__name__):
                fields = set(getattr(cls, "__dataclass_fields__", {}))
                self.assertEqual(fields & forbidden, set())

    def test_the_published_shape_carries_only_named_keys(self) -> None:
        payload = event(
            KIND_TOOL_APPROVAL_REQUESTED, approval=approval("Bash")
        ).to_dict()
        self.assertEqual(
            set(payload),
            {
                "version",
                "kind",
                "provider",
                "provider_session_id",
                "provider_sequence",
                "provider_event_id",
                "observed_at",
                "terminal",
                "text",
                "detail",
                "tool_name",
                "failure_code",
                "result",
                "request",
            },
        )


class LogTests(unittest.TestCase):
    def test_a_duplicate_event_id_is_recorded_once(self) -> None:
        log = DelegatedEventLog()
        first = event(KIND_ACTIVITY, 1, text="a", provider_event_id="u1")
        again = event(KIND_ACTIVITY, 2, text="a", provider_event_id="u1")
        self.assertIsNotNone(log.record(first))
        self.assertIsNone(log.record(again))
        self.assertEqual(len(log.events()), 1)
        self.assertEqual(log.duplicates, 1)

    def test_events_without_an_id_are_not_suppressed(self) -> None:
        """The safe direction: no id means no duplicate suppression, not a
        fabricated one."""
        log = DelegatedEventLog()
        log.record(event(KIND_ACTIVITY, 1, text="a"))
        log.record(event(KIND_ACTIVITY, 2, text="a"))
        self.assertEqual(len(log.events()), 2)

    def test_provider_order_is_recoverable_and_arrival_order_is_kept(self) -> None:
        log = DelegatedEventLog()
        log.record(event(KIND_ACTIVITY, 3, text="third"))
        log.record(event(KIND_ACTIVITY, 1, text="first"))
        self.assertEqual([e.text for e in log.events()], ["third", "first"])
        self.assertEqual([e.text for e in log.ordered()], ["first", "third"])
        self.assertEqual(log.out_of_order, 1)

    def test_nothing_is_recorded_after_a_terminal_event(self) -> None:
        log = DelegatedEventLog()
        log.record(event(KIND_CANCELLED, 1, text="stopped"))
        late = event(KIND_SUCCEEDED, 2, text="actually finished", result="r")
        self.assertIsNone(log.record(late))
        self.assertEqual(log.terminal_event.kind, KIND_CANCELLED)
        self.assertEqual(log.after_terminal, 1)

    def test_a_terminal_result_cannot_be_replaced_by_a_later_one(self) -> None:
        log = DelegatedEventLog()
        log.record(event(KIND_SUCCEEDED, 1, text="one", result="one"))
        log.record(event(KIND_PROVIDER_FAILED, 2, text="two"))
        self.assertEqual(log.terminal_event.result, "one")

    def test_the_buffer_is_bounded(self) -> None:
        log = DelegatedEventLog(max_events=4)
        for index in range(20):
            log.record(event(KIND_ACTIVITY, index, text="n" + str(index)))
        self.assertEqual(len(log.events()), 4)

    def test_a_non_event_is_refused_rather_than_stored(self) -> None:
        log = DelegatedEventLog()
        self.assertIsNone(log.record({"kind": "activity"}))
        self.assertEqual(log.refused, 1)


class ProjectionTests(unittest.TestCase):
    def test_output_and_success_are_meaningful_output(self) -> None:
        for kind, fields in (
            (KIND_OUTPUT, {"text": "hello"}),
            (KIND_SUCCEEDED, {"text": "done", "result": "done"}),
        ):
            with self.subTest(kind=kind):
                event_type, text, _ = projection(event(kind, **fields))
                self.assertEqual(event_type, EVENT_MEANINGFUL_OUTPUT)
                self.assertTrue(text)

    def test_a_request_to_a_person_projects_as_waiting(self) -> None:
        for kind, fields in (
            (KIND_CLARIFICATION_REQUESTED, {"clarification": clarification()}),
            (KIND_TOOL_APPROVAL_REQUESTED, {"approval": approval()}),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(projection(event(kind, **fields))[0], EVENT_WAITING_FOR_USER)

    def test_everything_else_is_progress(self) -> None:
        for kind in (KIND_SESSION_STARTED, KIND_ACTIVITY, KIND_CANCELLED):
            with self.subTest(kind=kind):
                self.assertEqual(projection(event(kind, text="x"))[0], EVENT_PROGRESS)

    def test_no_projection_claims_a_lifecycle_event_type(self) -> None:
        """Lifecycle types belong to Task Core; an adapter emitting one would be
        writing a completion into the history without passing the graph."""
        from cofferdam.workstation.tasks.models import CORE_OWNED_EVENT_TYPES

        for kind in DELEGATED_KINDS:
            fields = {}
            if kind == KIND_CLARIFICATION_REQUESTED:
                fields = {"clarification": clarification()}
            elif kind == KIND_TOOL_APPROVAL_REQUESTED:
                fields = {"approval": approval()}
            with self.subTest(kind=kind):
                self.assertNotIn(projection(event(kind, **fields))[0], CORE_OWNED_EVENT_TYPES)

    def test_option_labels_reach_the_history_text(self) -> None:
        built = event(
            KIND_CLARIFICATION_REQUESTED,
            clarification=clarification(options=[{"label": "rebase"}, {"label": "merge"}]),
        )
        _, text, _ = projection(built)
        self.assertIn("rebase", text)
        self.assertIn("merge", text)


class ResultTests(unittest.TestCase):
    def test_a_success_produces_a_result_with_provenance(self) -> None:
        built = event(
            KIND_SUCCEEDED,
            text="all done",
            result="all done",
            provider_session_id="session-abc",
        )
        result = result_from_event(task_id="tsk_1", event=built, completed_at=NOW)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.terminal_state, STATE_COMPLETED)
        self.assertEqual(result.result, "all done")
        self.assertEqual(result.provider_session_id, "session-abc")
        self.assertEqual(result.provider, "claude-agent-sdk")
        self.assertEqual(result.completed_at, NOW)

    def test_a_failure_produces_a_bounded_category_and_summary(self) -> None:
        built = event(KIND_PROVIDER_FAILED, text="x" * 5000, failure_code="claude_timeout")
        result = result_from_event(task_id="tsk_1", event=built, completed_at=NOW)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.terminal_state, STATE_FAILED)
        self.assertEqual(result.failure_code, "claude_timeout")
        self.assertLessEqual(
            len(result.failure_summary), delegated.MAX_FAILURE_SUMMARY_CHARS
        )
        self.assertIsNone(result.result)

    def test_a_cancellation_produces_a_cancelled_result_with_no_output(self) -> None:
        result = result_from_event(
            task_id="tsk_1", event=event(KIND_CANCELLED, text="stopped"), completed_at=NOW
        )
        self.assertEqual(result.terminal_state, STATE_CANCELLED)
        self.assertIsNone(result.result)
        self.assertIsNone(result.failure_code)

    def test_a_non_terminal_event_has_no_result(self) -> None:
        with self.assertRaises(DelegatedEventInvalid):
            result_from_event(
                task_id="tsk_1", event=event(KIND_ACTIVITY, text="working"), completed_at=NOW
            )

    def test_the_published_result_carries_no_stack_or_payload(self) -> None:
        result = result_from_event(
            task_id="tsk_1",
            event=event(KIND_PROVIDER_FAILED, text="broke", failure_code="claude_x"),
            completed_at=NOW,
        )
        payload = result.to_dict()
        for forbidden in ("traceback", "exception", "stack", "raw", "payload"):
            self.assertNotIn(forbidden, payload)


if __name__ == "__main__":
    unittest.main()
