"""Turning Agent SDK messages into Cofferdam's normalized events.

The SDK hands back typed objects — ``AssistantMessage`` holding ``TextBlock`` and
``ToolUseBlock``, ``ResultMessage`` carrying the outcome, a family of
``Task*Message`` progress notices. This module is the only place any of them is
read, and it produces :class:`~....delegated.DelegatedEvent` values and nothing
else. Downstream of here, nothing knows the provider exists.

Three properties this file is written to hold.

**It does not import the SDK.** Dispatch is by class name against a code-owned
table of the names verified in the published package, and every field is read
with ``getattr``. That is what lets the normalization — the part with all the
interesting behaviour — be tested on a machine where the SDK is not installed,
which is the stdlib-only CI machine. A normalizer that could only be tested with
the dependency present would be a normalizer mostly not tested.

**Recognised shapes only.** Every attribute read is named in source. An unknown
message class is *counted and dropped*, never coerced into an event: a stream
from a newer SDK will contain things this build has no words for, and inventing
words for them is how a provider's internals end up rendered on a phone.

**No payload survives.** No branch here puts a message object, a ``data``
dictionary, a tool input, or a thinking block into an event — the event classes
have nowhere to hold one, and this module never tries. Thinking blocks are
skipped without comment; the agent's private reasoning is not Cofferdam's to
collect, and the absence of a branch is the implementation of that.

The one thing that is not verified
-----------------------------------

Clarification. The SDK package contains no ``AskUserQuestion`` type and no schema
for one — that tool belongs to the CLI, and its input shape could not be read out
of the distribution the way every other name here was. So this module recognises
a question tool **conservatively**: it produces a clarification only when the
tool input contains an unambiguous question string, and otherwise degrades to
ordinary tool activity rather than inventing a question nobody asked.

It is also unreachable in this build by construction: the question tool is not in
:data:`~.options.PROFILE_TOOLS`, so the profile cannot produce one. Enabling it
needs the answer channel, and the answer channel is M2I PR2's subject, where the
schema will be verified against the real CLI before anything depends on it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ....runtime.identity import now_iso
from ...delegated import (
    KIND_ACTIVITY,
    KIND_CLARIFICATION_REQUESTED,
    KIND_OUTPUT,
    KIND_PROVIDER_FAILED,
    KIND_SESSION_STARTED,
    KIND_SUCCEEDED,
    KIND_TOOL_APPROVAL_REQUESTED,
    KIND_TOOL_FINISHED,
    KIND_TOOL_STARTED,
    MAX_QUESTION_CHARS,
    TOOL_CATEGORY_EXECUTE,
    TOOL_CATEGORY_NETWORK,
    TOOL_CATEGORY_OTHER,
    TOOL_CATEGORY_READ,
    TOOL_CATEGORY_WRITE,
    ClarificationRequest,
    DelegatedEvent,
    ToolApprovalRequest,
    build_event,
    safe_line,
    safe_text,
    valid_tool_name,
)

#: The provider id these events carry. One word, stable, and it appears in
#: durable records — so it is a constant here rather than a string literal
#: repeated at four call sites.
PROVIDER = "claude-agent-sdk"

# -- the recognised message classes ------------------------------------------
#
# Read from the published package. Dispatch is by ``type(message).__name__``
# rather than ``isinstance``, which is what keeps the SDK out of this module's
# imports — and, incidentally, what lets a test hand over a sanitized fake that
# is honest about its shape without subclassing anything.

MESSAGE_ASSISTANT = "AssistantMessage"
MESSAGE_USER = "UserMessage"
MESSAGE_SYSTEM = "SystemMessage"
MESSAGE_RESULT = "ResultMessage"
MESSAGE_STREAM_EVENT = "StreamEvent"
MESSAGE_RATE_LIMIT = "RateLimitEvent"

#: ``SystemMessage`` subclasses in the published package. Listed separately
#: because they are the ones carrying a task lifecycle rather than a bag of
#: metadata, and because a subclass name is what ``__name__`` reports.
MESSAGE_TASK_STARTED = "TaskStartedMessage"
MESSAGE_TASK_PROGRESS = "TaskProgressMessage"
MESSAGE_TASK_NOTIFICATION = "TaskNotificationMessage"
MESSAGE_TASK_UPDATED = "TaskUpdatedMessage"
MESSAGE_HOOK_EVENT = "HookEventMessage"
MESSAGE_MIRROR_ERROR = "MirrorErrorMessage"

KNOWN_MESSAGE_CLASSES: Tuple[str, ...] = (
    MESSAGE_ASSISTANT,
    MESSAGE_USER,
    MESSAGE_SYSTEM,
    MESSAGE_RESULT,
    MESSAGE_STREAM_EVENT,
    MESSAGE_RATE_LIMIT,
    MESSAGE_TASK_STARTED,
    MESSAGE_TASK_PROGRESS,
    MESSAGE_TASK_NOTIFICATION,
    MESSAGE_TASK_UPDATED,
    MESSAGE_HOOK_EVENT,
    MESSAGE_MIRROR_ERROR,
)

BLOCK_TEXT = "TextBlock"
BLOCK_THINKING = "ThinkingBlock"
BLOCK_TOOL_USE = "ToolUseBlock"
BLOCK_TOOL_RESULT = "ToolResultBlock"
BLOCK_SERVER_TOOL_USE = "ServerToolUseBlock"
BLOCK_SERVER_TOOL_RESULT = "ServerToolResultBlock"

#: How many content blocks of one message are read. A message with ten thousand
#: blocks is not something to iterate, and the tail of one is not information
#: anybody is missing.
MAX_BLOCKS = 64

#: Tool names that mean "the agent is asking a person a question" rather than
#: "the agent is doing something". Code-owned and currently unreachable: none of
#: these is in the running profile. See the module docstring.
QUESTION_TOOLS: Tuple[str, ...] = ("AskUserQuestion",)

#: What a tool would broadly do, for the one line a person reads before deciding
#: an approval. Coarse on purpose — see the vocabulary note in
#: :mod:`....delegated`. Anything unlisted is ``other``, which is the honest
#: answer for a tool this build has never heard of.
TOOL_CATEGORIES_BY_NAME: Dict[str, str] = {
    "Read": TOOL_CATEGORY_READ,
    "Glob": TOOL_CATEGORY_READ,
    "Grep": TOOL_CATEGORY_READ,
    "NotebookRead": TOOL_CATEGORY_READ,
    "Write": TOOL_CATEGORY_WRITE,
    "Edit": TOOL_CATEGORY_WRITE,
    "MultiEdit": TOOL_CATEGORY_WRITE,
    "NotebookEdit": TOOL_CATEGORY_WRITE,
    "Bash": TOOL_CATEGORY_EXECUTE,
    "BashOutput": TOOL_CATEGORY_EXECUTE,
    "KillShell": TOOL_CATEGORY_EXECUTE,
    "Task": TOOL_CATEGORY_EXECUTE,
    "WebFetch": TOOL_CATEGORY_NETWORK,
    "WebSearch": TOOL_CATEGORY_NETWORK,
}


def tool_category(name: Any) -> str:
    """Which coarse bucket a tool falls in. Never raises, never guesses wildly."""
    if not isinstance(name, str):
        return TOOL_CATEGORY_OTHER
    return TOOL_CATEGORIES_BY_NAME.get(name, TOOL_CATEGORY_OTHER)


class MessageNormalizer:
    """Turns one session's messages into normalized events, in order.

    Holds two pieces of state and no more: the provider sequence it has handed
    out, and the provider session id it has learned. Both are per session, which
    is why this is an object rather than a function — and it is deliberately not
    where ordering *policy* lives. Duplicate suppression and finality belong to
    :class:`~....delegated.DelegatedEventLog`, which is provider-neutral; this
    class only counts.

    ``unknown_messages`` is the conservative branch's tally. It is a number, not
    a sample: keeping an example of an unrecognised message would mean keeping a
    provider payload, which is the thing this package exists not to do.
    """

    def __init__(self, *, provider: str = PROVIDER) -> None:
        self._provider = provider
        self._sequence = 0
        self._session_id: Optional[str] = None
        self.unknown_messages = 0
        self.unknown_blocks = 0

    @property
    def provider_session_id(self) -> Optional[str]:
        return self._session_id

    def _next(self) -> int:
        self._sequence += 1
        return self._sequence

    def _event(self, kind: str, **fields: Any) -> DelegatedEvent:
        return build_event(
            kind=kind,
            provider=self._provider,
            provider_sequence=self._next(),
            observed_at=now_iso(),
            provider_session_id=self._session_id,
            **fields,
        )

    def _learn_session(self, message: Any) -> None:
        """Remember the provider's session id the first time it is reported.

        Never overwritten. A second, different id would mean the stream is no
        longer the session Cofferdam started, and quietly adopting it is how an
        adapter ends up reporting on somebody else's conversation. The mismatch
        is reported by the session runner, which owns the identity check; this
        method's job is simply not to lose the first answer.
        """
        if self._session_id is not None:
            return
        candidate = getattr(message, "session_id", None)
        if isinstance(candidate, str) and candidate:
            self._session_id = candidate

    # -- the entry point -----------------------------------------------------

    def normalize(self, message: Any) -> List[DelegatedEvent]:
        """Zero or more normalized events for one SDK message.

        Zero is a legitimate and common answer: a thinking block, an
        unrecognised system subtype, a message class from a newer SDK. Returning
        an empty list for those is the conservative behaviour, and it is the
        difference between a forward-compatible reader and one that turns every
        SDK release into an incident.
        """
        name = type(message).__name__
        if name not in KNOWN_MESSAGE_CLASSES:
            self.unknown_messages += 1
            return []
        self._learn_session(message)

        if name == MESSAGE_ASSISTANT:
            return self._assistant(message)
        if name == MESSAGE_USER:
            return self._user(message)
        if name == MESSAGE_RESULT:
            return self._result(message)
        if name in (
            MESSAGE_TASK_STARTED,
            MESSAGE_TASK_PROGRESS,
            MESSAGE_TASK_NOTIFICATION,
            MESSAGE_TASK_UPDATED,
        ):
            return self._task(message, name)
        if name == MESSAGE_SYSTEM:
            return self._system(message)
        if name == MESSAGE_RATE_LIMIT:
            # Counted, not narrated. The payload carries reset timestamps and
            # quota figures that are nobody's business on a task screen.
            return [
                self._event(
                    KIND_ACTIVITY,
                    text="Waiting on Claude rate limits.",
                    provider_event_id=_event_id(message),
                )
            ]
        if name == MESSAGE_MIRROR_ERROR:
            # Cofferdam configures no session store, so this cannot arrive from
            # anything it asked for. Counted rather than reported.
            self.unknown_messages += 1
            return []
        # `StreamEvent` needs `include_partial_messages`, and `HookEventMessage`
        # needs `include_hook_events`; the profile sets both to False. Counted
        # rather than parsed, so turning one on by accident shows up as a number
        # instead of a flood.
        self.unknown_messages += 1
        return []

    # -- per message class ---------------------------------------------------

    def _assistant(self, message: Any) -> List[DelegatedEvent]:
        events: List[DelegatedEvent] = []
        for index, block in enumerate(_blocks(message)):
            block_name = type(block).__name__
            if block_name == BLOCK_TEXT:
                text = safe_text(getattr(block, "text", None), MAX_QUESTION_CHARS * 4)
                if text:
                    events.append(
                        self._event(
                            KIND_OUTPUT,
                            text=text,
                            provider_event_id=_block_id(message, index),
                        )
                    )
            elif block_name == BLOCK_THINKING:
                # Skipped, deliberately and silently. See the module docstring.
                continue
            elif block_name in (BLOCK_TOOL_USE, BLOCK_SERVER_TOOL_USE):
                events.append(self._tool_use(message, block, index))
            elif block_name == BLOCK_TOOL_RESULT:
                events.append(self._tool_result(block, message, index))
            else:
                self.unknown_blocks += 1
        return events

    def _user(self, message: Any) -> List[DelegatedEvent]:
        """Tool results arrive as user messages. Report the outcome, not the body.

        A tool result can be an entire file. Putting it in a task timeline would
        make the default view the terminal this product refuses to build, so only
        the shape is recorded: something came back, and whether it failed.
        """
        events: List[DelegatedEvent] = []
        for index, block in enumerate(_blocks(message)):
            if type(block).__name__ != BLOCK_TOOL_RESULT:
                continue
            events.append(self._tool_result(block, message, index))
        return events

    def _tool_use(self, message: Any, block: Any, index: int) -> DelegatedEvent:
        name = getattr(block, "name", None)
        if isinstance(name, str) and name in QUESTION_TOOLS:
            clarification = clarification_from_tool_input(getattr(block, "input", None))
            if clarification is not None:
                return self._event(
                    KIND_CLARIFICATION_REQUESTED,
                    text=clarification.question,
                    clarification=clarification,
                    provider_event_id=_block_id(message, index),
                )
            # A question tool whose input this build cannot read is reported as
            # ordinary tool activity. Not as a clarification with an invented
            # question, and not as an error: the agent did something, Cofferdam
            # simply has no verified words for what it asked.
        # Validated *before* it is written into a sentence. Dropping the field
        # while leaving the name in the text would be half a check.
        label = name if valid_tool_name(name) else None
        return self._event(
            KIND_TOOL_STARTED,
            text=("Claude used " + label + ".") if label else "Claude used a tool.",
            tool_name=label,
            provider_event_id=_block_id(message, index),
        )

    def _tool_result(self, block: Any, message: Any, index: int) -> DelegatedEvent:
        is_error = getattr(block, "is_error", None) is True
        return self._event(
            KIND_TOOL_FINISHED,
            text="A tool reported an error." if is_error else "A tool finished.",
            detail="error" if is_error else "ok",
            provider_event_id=_block_id(message, index),
        )

    def _system(self, message: Any) -> List[DelegatedEvent]:
        subtype = getattr(message, "subtype", None)
        if subtype == "init":
            return [
                self._event(
                    KIND_SESSION_STARTED,
                    text="Claude session ready.",
                    provider_event_id=_event_id(message),
                )
            ]
        # Every other system subtype is metadata whose ``data`` dictionary is
        # exactly what must not be stored. Counted.
        self.unknown_messages += 1
        return []

    def _task(self, message: Any, name: str) -> List[DelegatedEvent]:
        """Background-task lifecycle notices, as one bounded activity line each.

        The SDK reports these for tasks the *agent* spawns, which are not
        Cofferdam tasks and must never be confused with them — hence
        ``activity`` rather than any lifecycle kind. The description is the only
        field read; usage figures, output file paths and patch dictionaries are
        left where they are.
        """
        description = safe_line(getattr(message, "description", None), 200)
        if name == MESSAGE_TASK_NOTIFICATION:
            status = safe_line(getattr(message, "status", None), 40)
            summary = safe_line(getattr(message, "summary", None), 200)
            text = "A Claude sub-task " + (status or "finished") + "."
            return [
                self._event(
                    KIND_ACTIVITY,
                    text=text,
                    detail=summary,
                    provider_event_id=_event_id(message),
                )
            ]
        if name == MESSAGE_TASK_UPDATED:
            status = safe_line(getattr(message, "status", None), 40)
            return [
                self._event(
                    KIND_ACTIVITY,
                    text="A Claude sub-task changed state.",
                    detail=status,
                    provider_event_id=_event_id(message),
                )
            ]
        return [
            self._event(
                KIND_ACTIVITY,
                text=description or "Claude is working.",
                provider_event_id=_event_id(message),
            )
        ]

    def _result(self, message: Any) -> List[DelegatedEvent]:
        """The one message that ends a turn, and the only source of a result.

        Two rules, both learned by the Claude Code adapter first and both worth
        repeating here because the SDK does not enforce either.

        **A missing ``is_error`` is an error.** An absent field must never be the
        reason a task is reported complete, so the check is ``is not False``
        rather than ``is True``.

        **Success with nothing to show is a failure.** ``is_error`` false and no
        result text means there is no result to report, and an empty completion
        would read as work that produced nothing when in fact nothing was
        reported.
        """
        is_error = getattr(message, "is_error", None) is not False
        text = safe_text(getattr(message, "result", None), 16000)
        event_id = _event_id(message)

        if is_error:
            subtype = safe_line(getattr(message, "subtype", None), 60)
            code = _failure_code(subtype)
            return [
                self._event(
                    KIND_PROVIDER_FAILED,
                    text=text or "Claude reported an error without explaining it.",
                    detail=subtype,
                    failure_code=code,
                    provider_event_id=event_id,
                )
            ]
        if not text:
            return [
                self._event(
                    KIND_PROVIDER_FAILED,
                    text="Claude finished without a result to show.",
                    failure_code="empty_result",
                    provider_event_id=event_id,
                )
            ]
        return [
            self._event(
                KIND_SUCCEEDED,
                text=text,
                result=text,
                provider_event_id=event_id,
            )
        ]


# -- the permission channel --------------------------------------------------


def approval_request(
    tool_name: Any, *, reason: Any = None
) -> Optional[ToolApprovalRequest]:
    """One tool approval request, from the SDK's permission callback.

    Built from the tool **name** and a bounded reason, and from nothing else.
    The callback also receives the tool's input — the command, the path, the
    arguments — and none of it is read here. That material is the reason
    approvals stay on a trusted surface, and copying it into a durable event
    that a phone renders would defeat the arrangement it exists to protect.

    Returns ``None`` for a request that does not name a usable tool, because an
    approval nobody could decide is not worth recording as one.
    """
    if not isinstance(tool_name, str):
        return None
    try:
        return ToolApprovalRequest.from_dict(
            {
                "category": "tool_approval",
                "tool_name": tool_name,
                "tool_category": tool_category(tool_name),
                "reason": reason,
            }
        )
    except ValueError:
        return None


def approval_event(
    *,
    request: ToolApprovalRequest,
    provider_sequence: int,
    provider_session_id: Optional[str] = None,
    provider_event_id: Optional[str] = None,
    provider: str = PROVIDER,
) -> DelegatedEvent:
    """The normalized event for a denied-and-reported tool approval."""
    return build_event(
        kind=KIND_TOOL_APPROVAL_REQUESTED,
        provider=provider,
        provider_sequence=provider_sequence,
        observed_at=now_iso(),
        provider_session_id=provider_session_id,
        provider_event_id=provider_event_id,
        text="Claude asked for permission to use " + request.tool_name + ".",
        tool_name=request.tool_name,
        approval=request,
    )


# -- clarification, read conservatively --------------------------------------


def clarification_from_tool_input(payload: Any) -> Optional[ClarificationRequest]:
    """A clarification, only if the input unambiguously contains one.

    The schema of the CLI's question tool is **not verified** — it is not in the
    SDK distribution — so this reader accepts only shapes where a question string
    is unmistakable, and returns ``None`` for everything else. ``None`` costs a
    clarification that becomes an ordinary activity line; a looser reader would
    cost a *fabricated* question shown to somebody as if the agent had asked it,
    and those two mistakes are not the same size.

    Whatever this returns still goes through
    :meth:`~....delegated.ClarificationRequest.from_dict`, so it cannot produce
    something carrying a tool field even if a future shape contains one.
    """
    if not isinstance(payload, dict):
        return None
    question = payload.get("question")
    options = payload.get("options")
    if not isinstance(question, str):
        # The plural form: a list of question objects, first one taken. Bounded
        # to the first because a task waiting on four questions at once has no
        # answer channel in this build to answer even one.
        questions = payload.get("questions")
        if isinstance(questions, (list, tuple)) and questions:
            first = questions[0]
            if isinstance(first, dict):
                question = first.get("question")
                options = first.get("options")
    if not isinstance(question, str) or not question.strip():
        return None
    try:
        return ClarificationRequest.from_dict(
            {
                "category": "clarification",
                "question": question,
                "options": options if isinstance(options, (list, tuple)) else [],
                "allows_free_text": payload.get("allows_free_text") is not False,
            }
        )
    except ValueError:
        return None


# -- small readers -----------------------------------------------------------


def _blocks(message: Any) -> Sequence[Any]:
    """The content blocks of a message, bounded, or nothing.

    A string ``content`` — which the SDK's ``UserMessage`` permits — yields no
    blocks rather than being wrapped into a synthetic text block. There is
    nothing in this adapter that needs a user message's own text: Cofferdam
    already holds the prompt it sent, and echoing it back into the task history
    would duplicate user content into a second place.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return ()
    return list(content)[:MAX_BLOCKS]


def _event_id(message: Any) -> Optional[str]:
    """The provider's own id for this message, used for duplicate suppression.

    ``None`` when absent, which simply means this event is not suppressible —
    the safe direction, since the alternative would be inventing an id and
    suppressing something that was not a duplicate.
    """
    candidate = getattr(message, "uuid", None)
    return candidate if isinstance(candidate, str) and candidate else None


def _block_id(message: Any, index: int) -> Optional[str]:
    """A stable id for one block of one message, or ``None``.

    Derived from the message's id and the block's position, because a message
    carrying three text blocks would otherwise offer one id for three events and
    two of them would look like duplicates of the first.
    """
    base = _event_id(message)
    if base is None:
        return None
    return base + "." + str(index)


def _failure_code(subtype: Optional[str]) -> str:
    """A failure code that does not contradict itself.

    ``is_error`` true with subtype ``success`` is a combination the CLI really
    produces, and ``claude_success`` is a nonsense code for a failure. A
    contradictory or unusable subtype becomes the generic code instead.
    """
    if not subtype or subtype == "success":
        return "provider_error"
    cleaned = "".join(
        character if character.isalnum() else "_" for character in subtype.lower()
    ).strip("_")
    return ("claude_" + cleaned) if cleaned else "provider_error"


__all__ = [
    "BLOCK_TEXT",
    "BLOCK_THINKING",
    "BLOCK_TOOL_RESULT",
    "BLOCK_TOOL_USE",
    "KNOWN_MESSAGE_CLASSES",
    "MAX_BLOCKS",
    "PROVIDER",
    "QUESTION_TOOLS",
    "TOOL_CATEGORIES_BY_NAME",
    "MessageNormalizer",
    "approval_event",
    "approval_request",
    "clarification_from_tool_input",
    "tool_category",
]
