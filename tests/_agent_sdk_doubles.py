"""Sanitized doubles for the Claude Agent SDK, and the sessions built on it.

Two kinds of double live here, and the difference matters.

**Message doubles** are *shape-accurate and content-safe*. Their class names and
attribute names were read from the published ``claude-agent-sdk`` distribution
— ``AssistantMessage`` holding ``TextBlock`` and ``ToolUseBlock``,
``ResultMessage`` with ``is_error``/``result``/``session_id``, the ``Task*``
family — because the normalizer dispatches on exactly those names, and a double
that got one wrong would let the whole suite pass while the real stream produced
nothing. What is *not* copied from the SDK is any content: every string in this
file is invented, none of it came from a transcript, and nothing here reaches a
network, a model, an account or a subprocess.

They are plain classes rather than SDK subclasses on purpose. The normalizer must
work on a machine where the SDK is not installed — that is the stdlib-only CI
machine — so the tests that exercise it must too.

**Session doubles** replace the thread-and-event-loop half. :class:`FakeSession`
implements the same synchronous boundary the adapter is written against, so
ordering, cancellation precedence, terminal finality and resource release are all
testable without a subprocess. What it cannot prove is that the real
:class:`~cofferdam...session.SdkSession` drives the SDK correctly; that is what
the option-policy tests and, eventually, a supervised live spike are for, and
saying so here is more useful than pretending otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from cofferdam.workstation.tasks.adapters.claude_agent_sdk.session import (
    DelegatedSession,
    SessionRefused,
)
from cofferdam.workstation.tasks.delegated import DelegatedEvent

# -- content blocks ----------------------------------------------------------


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    """Present so a test can prove it is *skipped*.

    The one block whose correct handling is to produce nothing at all: the
    agent's private reasoning is not Cofferdam's to store, and a test that only
    checked the blocks that do produce events could not tell the difference
    between "skipped" and "never sent".
    """

    thinking: str
    signature: str = "sig"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: Optional[bool] = None


# -- messages ----------------------------------------------------------------


@dataclass
class AssistantMessage:
    content: List[Any]
    model: str = "claude-test"
    session_id: Optional[str] = "session-abc"
    uuid: Optional[str] = None


@dataclass
class UserMessage:
    content: Any
    uuid: Optional[str] = None
    session_id: Optional[str] = None


@dataclass
class SystemMessage:
    subtype: str
    data: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = "session-abc"
    uuid: Optional[str] = None


@dataclass
class TaskProgressMessage:
    subtype: str = "task_progress"
    data: Dict[str, Any] = field(default_factory=dict)
    task_id: str = "sub-1"
    description: str = "Reading files"
    usage: Dict[str, Any] = field(default_factory=dict)
    uuid: str = "u-task"
    session_id: str = "session-abc"


@dataclass
class TaskNotificationMessage:
    subtype: str = "task_notification"
    data: Dict[str, Any] = field(default_factory=dict)
    task_id: str = "sub-1"
    status: str = "completed"
    output_file: str = "/tmp/should-never-be-read"
    summary: str = "did the thing"
    uuid: str = "u-note"
    session_id: str = "session-abc"


@dataclass
class TaskUpdatedMessage:
    subtype: str = "task_updated"
    data: Dict[str, Any] = field(default_factory=dict)
    task_id: str = "sub-1"
    patch: Dict[str, Any] = field(default_factory=dict)
    status: Optional[str] = "running"
    session_id: Optional[str] = "session-abc"
    uuid: Optional[str] = "u-upd"


@dataclass
class ResultMessage:
    subtype: str = "success"
    duration_ms: int = 10
    duration_api_ms: int = 5
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "session-abc"
    result: Optional[str] = "done"
    uuid: Optional[str] = "u-result"
    permission_denials: Optional[List[Any]] = None
    total_cost_usd: Optional[float] = None


@dataclass
class StreamEvent:
    """Only reachable with ``include_partial_messages``, which the profile
    disables. Here so a test can prove it is counted rather than parsed."""

    uuid: str = "u-stream"
    session_id: str = "session-abc"
    event: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SomethingFromANewerSdk:
    """A class name this build has never heard of. Must be dropped, not coerced."""

    anything: str = "x"


# -- the module double -------------------------------------------------------


class FakePermissionResultDeny:
    def __init__(self, *, behavior: str = "deny", message: str = "", interrupt: bool = False):
        self.behavior = behavior
        self.message = message
        self.interrupt = interrupt


class FakeClaudeAgentOptions:
    """Accepts exactly what the real dataclass accepts, and records it.

    Keyword-only and permissive about *values* but not about *names*: an option
    name this adapter invented would raise here, which is the check that keeps
    ``build_option_values`` honest without the SDK installed.
    """

    ALLOWED = frozenset(
        {
            "tools",
            "allowed_tools",
            "disallowed_tools",
            "system_prompt",
            "mcp_servers",
            "strict_mcp_config",
            "permission_mode",
            "continue_conversation",
            "resume",
            "session_id",
            "max_turns",
            "max_budget_usd",
            "model",
            "fallback_model",
            "betas",
            "permission_prompt_tool_name",
            "cwd",
            "cli_path",
            "settings",
            "add_dirs",
            "env",
            "extra_args",
            "max_buffer_size",
            "debug_stderr",
            "stderr",
            "can_use_tool",
            "hooks",
            "user",
            "include_partial_messages",
            "include_hook_events",
            "fork_session",
            "agents",
            "setting_sources",
            "skills",
            "sandbox",
            "plugins",
            "max_thinking_tokens",
            "thinking",
            "effort",
            "output_format",
            "enable_file_checkpointing",
            "session_store",
            "session_store_flush",
            "load_timeout_ms",
            "task_budget",
        }
    )

    def __init__(self, **values: Any) -> None:
        unknown = sorted(set(values) - self.ALLOWED)
        if unknown:
            raise TypeError("unexpected option: " + unknown[0])
        self.values = dict(values)
        for name, value in values.items():
            setattr(self, name, value)


class FakeSdkModule:
    """Stands in for the imported ``claude_agent_sdk`` module.

    Carries every attribute :data:`REQUIRED_ATTRIBUTES` names, so a test can
    check the loader's happy path, and can be constructed missing one so the
    "incompatible version" refusal can be checked too.
    """

    def __init__(self, *, omit: Sequence[str] = ()) -> None:
        self.__version__ = "0.2.134"
        attributes = {
            "ClaudeSDKClient": object,
            "ClaudeAgentOptions": FakeClaudeAgentOptions,
            "AssistantMessage": AssistantMessage,
            "UserMessage": UserMessage,
            "SystemMessage": SystemMessage,
            "ResultMessage": ResultMessage,
            "TextBlock": TextBlock,
            "ToolUseBlock": ToolUseBlock,
            "ToolResultBlock": ToolResultBlock,
            "PermissionResultDeny": FakePermissionResultDeny,
            "ClaudeSDKError": RuntimeError,
        }
        for name, value in attributes.items():
            if name in omit:
                continue
            setattr(self, name, value)


# -- session doubles ---------------------------------------------------------


class FakeSession(DelegatedSession):
    """A delegated session with a scripted event stream and no provider at all.

    Events are handed over in batches: each :meth:`drain` returns the next batch,
    which is how a test expresses "and then, later, this happened" without
    sleeping. ``cancel_succeeds`` exists because the interesting cancellation
    cases are the ones where it does *not*.
    """

    def __init__(
        self,
        *,
        task_id: str = "task",
        batches: Optional[Sequence[Sequence[DelegatedEvent]]] = None,
        cancel_succeeds: bool = True,
        cancel_events: Sequence[DelegatedEvent] = (),
        start_error: Optional[str] = None,
        provider_session_id: Optional[str] = "session-abc",
    ) -> None:
        self.task_id = task_id
        self._batches: List[List[DelegatedEvent]] = [
            list(batch) for batch in (batches or [])
        ]
        self._cancel_succeeds = cancel_succeeds
        self._cancel_events = list(cancel_events)
        self._start_error = start_error
        self.provider_session_id = provider_session_id
        self.started_with: Optional[str] = None
        self.cancel_calls = 0
        self.close_calls = 0
        self._finished = False

    def start(self, prompt: str) -> None:
        if self._start_error is not None:
            raise SessionRefused(self._start_error)
        self.started_with = prompt

    def drain(self) -> List[DelegatedEvent]:
        if not self._batches:
            return []
        return list(self._batches.pop(0))

    def request_cancel(self) -> bool:
        self.cancel_calls += 1
        if self._cancel_succeeds:
            self._batches.insert(0, list(self._cancel_events))
        return self._cancel_succeeds

    def close(self) -> bool:
        self.close_calls += 1
        self._finished = True
        return True

    def finish(self) -> None:
        """Mark the session stopped without producing anything.

        The "process ended and said nothing" case, which must be a truthful
        failure rather than an empty completion.
        """
        self._finished = True

    @property
    def finished(self) -> bool:
        return self._finished


# -- helper-process doubles --------------------------------------------------


class _FakeStdin:
    """The write half of a pipe, recording whole protocol lines."""

    def __init__(self, helper: "FakeHelperProcess") -> None:
        self._helper = helper
        self.closed = False

    def write(self, data: str) -> int:
        if self.closed:
            raise OSError("closed")
        self._helper.received.append(data)
        self._helper.handle(data)
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise OSError("closed")

    def close(self) -> None:
        self.closed = True
        self._helper.finish()


class _FakeStdout:
    """The read half, iterated by the parent's reader thread until it ends."""

    def __init__(self, helper: "FakeHelperProcess") -> None:
        self._helper = helper

    def __iter__(self):
        while True:
            line = self._helper.queue.get()
            if line is None:
                return
            yield line


class FakeHelperProcess:
    """A helper that speaks :mod:`.hostproto` without being a process.

    Everything the parent side does — the two acknowledgements, event framing,
    answer routing, close escalation — is exercised against this, so the whole
    of ``hostclient.py`` is testable on a machine with no SDK, no CLI and no
    subprocess. What it cannot prove is that the *real* helper drives the SDK
    correctly; that is what the option-policy tests and a supervised live spike
    are for, and saying so here is more useful than pretending otherwise.

    ``session_id`` is reported at start and never changes, so a test can assert
    that answering a question does not begin a new provider session.
    """

    def __init__(
        self,
        *,
        session_id: str = "sess-1",
        ready: bool = True,
        error: Optional[str] = None,
        question_token: Optional[str] = None,
    ) -> None:
        import queue as _queue

        from cofferdam.workstation.tasks.adapters.claude_agent_sdk import hostproto

        self._hostproto = hostproto
        self.queue: "_queue.Queue" = _queue.Queue()
        self.session_id = session_id
        self.received: List[str] = []
        self.answers: List[Dict[str, Any]] = []
        self.cancelled = 0
        self.closed = 0
        self.terminated = 0
        self.killed = 0
        self.returncode: Optional[int] = None
        self._question_token = question_token
        self._error = error
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        if ready:
            self.emit(hostproto.message(hostproto.MESSAGE_READY))
        if error is not None:
            self.emit(hostproto.message(hostproto.MESSAGE_ERROR, detail=error))

    # -- speaking ------------------------------------------------------------

    def emit(self, payload: Dict[str, Any]) -> None:
        self.queue.put(self._hostproto.encode_line(payload))

    def emit_event(self, event: DelegatedEvent) -> None:
        self.emit(self._hostproto.event_payload(event))

    def finish(self) -> None:
        self.queue.put(None)

    # -- listening -----------------------------------------------------------

    def handle(self, raw: str) -> None:
        parsed = self._hostproto.decode_line(raw)
        if parsed is None:
            return
        name = parsed.get("command")
        if name == self._hostproto.COMMAND_START:
            if self._error is not None:
                return
            self.emit(
                self._hostproto.message(
                    self._hostproto.MESSAGE_STARTED,
                    provider_session_id=self.session_id,
                )
            )
            if self._question_token is not None:
                self.emit_event(self._clarification())
        elif name == self._hostproto.COMMAND_ANSWER:
            self.answers.append(
                {"token": parsed.get("token"), "answer": parsed.get("answer")}
            )
        elif name == self._hostproto.COMMAND_CANCEL:
            self.cancelled += 1
        elif name == self._hostproto.COMMAND_CLOSE:
            self.closed += 1

    def _clarification(self) -> DelegatedEvent:
        from cofferdam.workstation.tasks.delegated import (
            KIND_CLARIFICATION_REQUESTED,
            ClarificationRequest,
            build_event,
        )

        return build_event(
            kind=KIND_CLARIFICATION_REQUESTED,
            provider="claude-agent-sdk",
            provider_sequence=1,
            observed_at="2026-08-09T00:00:00Z",
            provider_session_id=self.session_id,
            provider_event_id=self._question_token,
            text="Which label?",
            clarification=ClarificationRequest.from_dict(
                {
                    "category": "clarification",
                    "question": "Which label?",
                    "options": [
                        {"label": "alpha", "value": "alpha", "option_id": "opt1"},
                        {"label": "beta", "value": "beta", "option_id": "opt2"},
                    ],
                    "answer_mode": "single_choice",
                }
            ),
        )

    # -- the Popen surface ---------------------------------------------------

    def wait(self, timeout: Optional[float] = None) -> int:
        if self.returncode is None:
            # Nothing has stopped it: report the timeout a real one would, so
            # the parent's escalation is genuinely exercised.
            raise TimeoutError("still running")
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = -15
        self.finish()

    def kill(self) -> None:  # pragma: no cover - reached only if terminate fails
        self.killed += 1
        self.returncode = -9
        self.finish()

    def exit(self, code: int = 0) -> None:
        """Let the helper stop of its own accord."""
        self.returncode = code
        self.finish()


__all__ = [
    "AssistantMessage",
    "FakeClaudeAgentOptions",
    "FakeHelperProcess",
    "FakePermissionResultDeny",
    "FakeSdkModule",
    "FakeSession",
    "ResultMessage",
    "SomethingFromANewerSdk",
    "StreamEvent",
    "SystemMessage",
    "TaskNotificationMessage",
    "TaskProgressMessage",
    "TaskUpdatedMessage",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "UserMessage",
]
