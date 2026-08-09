"""The bounded protocol between the adapter and its Agent SDK helper process.

Small, closed, and newline-delimited JSON in both directions. Two vocabularies —
:data:`COMMANDS` going down, :data:`MESSAGES` coming up — and a word outside
either is refused rather than interpreted.

The property that makes this worth having
-----------------------------------------

**Nothing that crosses this pipe is a provider object.** The helper normalizes
before it speaks: what travels upward is the same bounded, sanitized,
provider-neutral :class:`~....delegated.DelegatedEvent` shape that Task Core
already stores, rebuilt on the parent side through the *same* constructors that
validate an event built in-process. So "a raw SDK payload cannot reach the
daemon" is not a rule the helper has to remember — there is no message type that
could carry one, and the reader would refuse it if there were.

That is stronger than it sounds. The helper is where the SDK lives, and the
helper is a different process with a different environment; a bug there cannot
put an unbounded string into the daemon's memory, because the reader bounds every
line before it is parsed and every field after it is.

What is deliberately absent
---------------------------

No command carries a path, an executable, an environment, a tool name, a
permission decision, a session identifier or a CLI flag. The helper's entire
configuration is decided when it is *launched* — fixed argv, code-owned
environment — and everything after that is a prompt, an answer, a cancel and a
close. There is nothing on this channel that could reconfigure what the agent may
do, which is why the channel can be as simple as it is.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ...delegated import (
    CATEGORY_CLARIFICATION,
    CATEGORY_TOOL_APPROVAL,
    DELEGATED_KINDS,
    ClarificationRequest,
    DelegatedEvent,
    DelegatedEventInvalid,
    ToolApprovalRequest,
    build_event,
)

#: Bumped when the protocol changes shape. Checked on every line: a helper from a
#: different build is refused rather than half-understood, which matters because
#: the two halves are deployed as one file tree but run as two processes and
#: could in principle be mismatched during an upgrade.
#:
#: Version 2 adds ``followup`` going down and ``turn_complete`` coming up. The
#: bump is required even though both additions are new words rather than changed
#: ones: a version-1 helper would silently drop a ``followup`` command as
#: unknown, and a parent that had sent one would then wait for a turn that is
#: never going to start. Refusing the mismatched pair outright is the honest
#: failure — the task reports that the session is unavailable, which is true.
PROTOCOL_VERSION = 2

#: The most bytes one protocol line may occupy. Generous enough for a bounded
#: result — sixteen thousand characters of text plus its envelope — and small
#: enough that a helper streaming without limit is refused rather than absorbed.
MAX_LINE_BYTES = 96 * 1024

# -- parent to helper --------------------------------------------------------

COMMAND_START = "start"
COMMAND_ANSWER = "answer"
#: One more user turn for a session that has finished the previous one and is
#: waiting for nothing.
#:
#: Its own command, and **not** a second use of ``answer``. The two carry text
#: to the same client and are otherwise nothing alike: an answer resolves a
#: question the agent is blocked on and must match the token of that question,
#: while a follow-up is a new instruction that is only legal when no question is
#: open. One command doing both would have to decide which from state, at the
#: moment when getting it wrong means either answering a question with an
#: unrelated instruction or losing somebody's message into a callback.
#:
#: It carries a bounded text and a turn number, and nothing else. There is no
#: session id on it — the helper has exactly one client and no way to reach
#: another — and no option, tool, mode or path.
COMMAND_FOLLOWUP = "followup"
COMMAND_CANCEL = "cancel"
COMMAND_CLOSE = "close"

COMMANDS = (
    COMMAND_START,
    COMMAND_ANSWER,
    COMMAND_FOLLOWUP,
    COMMAND_CANCEL,
    COMMAND_CLOSE,
)

# -- helper to parent --------------------------------------------------------

#: The helper has imported everything, verified its own environment, and is
#: waiting for a command. Sent before anything else; a helper that never sends it
#: is a helper that never became usable, and the parent says so rather than
#: waiting on a process that will not answer.
MESSAGE_READY = "ready"
#: The session connected and the prompt was delivered.
MESSAGE_STARTED = "started"
#: One normalized delegated event.
MESSAGE_EVENT = "event"
#: One bounded observation of a tool input this build could not read. Names,
#: types and counts — never a value. See :mod:`.question`.
MESSAGE_OBSERVATION = "observation"
#: A turn ended and the client is still connected. Carries the provider session
#: id and the turn number the helper believes it just finished, so the parent
#: can check that a second turn happened in the same conversation as the first
#: rather than assuming it.
MESSAGE_TURN_COMPLETE = "turn_complete"
#: The helper is refusing or reporting a failure, in Cofferdam's words.
MESSAGE_ERROR = "error"
#: The session reached a terminal event and the helper is shutting down.
MESSAGE_FINISHED = "finished"

MESSAGES = (
    MESSAGE_READY,
    MESSAGE_STARTED,
    MESSAGE_EVENT,
    MESSAGE_OBSERVATION,
    MESSAGE_TURN_COMPLETE,
    MESSAGE_ERROR,
    MESSAGE_FINISHED,
)


class ProtocolError(ValueError):
    """A line that could not be understood, with a sentence naming the category.

    Never carries the offending line. A protocol error is a thing that gets
    logged, and a message containing the payload that caused it would be a way to
    put arbitrary text into a log by sending a malformed frame.
    """


def encode_line(payload: Dict[str, Any]) -> str:
    """One protocol line, ASCII-safe and newline-terminated.

    ``ensure_ascii=True`` escapes every non-ASCII character to a ``\\uXXXX``
    sequence, so a Turkish question survives regardless of what either process's
    pipe encoding turns out to be — the bytes are ASCII and the reader's own JSON
    parser reconstitutes the characters. The Claude Code adapter learned this the
    same way and does the same thing on its stdin.
    """
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"


def decode_line(raw: Any) -> Optional[Dict[str, Any]]:
    """One protocol line as a dictionary, or ``None`` if it is not one.

    ``None`` rather than an exception for a malformed line, because the caller's
    correct response is always the same — count it and read the next one — and a
    reader that raised would turn one bad frame into a dead session.
    """
    if not isinstance(raw, str):
        return None
    if len(raw.encode("utf-8", "replace")) > MAX_LINE_BYTES:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("v") != PROTOCOL_VERSION:
        return None
    return parsed


def command(name: str, **fields: Any) -> Dict[str, Any]:
    if name not in COMMANDS:
        raise ProtocolError("unknown command")
    payload: Dict[str, Any] = {"v": PROTOCOL_VERSION, "command": name}
    payload.update(fields)
    return payload


def message(name: str, **fields: Any) -> Dict[str, Any]:
    if name not in MESSAGES:
        raise ProtocolError("unknown message")
    payload: Dict[str, Any] = {"v": PROTOCOL_VERSION, "message": name}
    payload.update(fields)
    return payload


def event_payload(event: DelegatedEvent) -> Dict[str, Any]:
    """One normalized event as a protocol message.

    Built from :meth:`DelegatedEvent.to_dict`, which already has nowhere to put a
    provider object — so this function cannot smuggle one even by accident.
    """
    return message(MESSAGE_EVENT, event=event.to_dict())


def event_from_payload(payload: Any) -> Optional[DelegatedEvent]:
    """Rebuild one event from a protocol message, or refuse it.

    Rebuilt through :func:`~....delegated.build_event` rather than by assigning
    fields, so an event that arrived over a pipe passes exactly the same bounds,
    sanitization and per-kind exclusivity checks as one built in-process. A helper
    that had been replaced by something hostile could not use this channel to
    write an event Task Core would not otherwise accept.
    """
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    if kind not in DELEGATED_KINDS:
        return None

    request = payload.get("request")
    clarification = None
    approval = None
    if isinstance(request, dict):
        try:
            if request.get("category") == CATEGORY_CLARIFICATION:
                clarification = ClarificationRequest.from_dict(request)
            elif request.get("category") == CATEGORY_TOOL_APPROVAL:
                approval = ToolApprovalRequest.from_dict(request)
        except DelegatedEventInvalid:
            # A request that will not rebuild is dropped and the event is not.
            # The alternative — refusing the whole event — would lose the fact
            # that the agent asked something, which is the part a person needs.
            return None

    try:
        return build_event(
            kind=kind,
            provider=payload.get("provider") or "unknown",
            provider_sequence=int(payload.get("provider_sequence") or 0),
            observed_at=payload.get("observed_at") or "",
            provider_session_id=payload.get("provider_session_id"),
            provider_event_id=payload.get("provider_event_id"),
            text=payload.get("text"),
            detail=payload.get("detail"),
            tool_name=payload.get("tool_name"),
            clarification=clarification,
            approval=approval,
            failure_code=payload.get("failure_code"),
            result=payload.get("result"),
        )
    except (DelegatedEventInvalid, TypeError, ValueError):
        return None


__all__ = [
    "COMMANDS",
    "COMMAND_ANSWER",
    "COMMAND_CANCEL",
    "COMMAND_CLOSE",
    "COMMAND_FOLLOWUP",
    "COMMAND_START",
    "MAX_LINE_BYTES",
    "MESSAGES",
    "MESSAGE_ERROR",
    "MESSAGE_EVENT",
    "MESSAGE_FINISHED",
    "MESSAGE_OBSERVATION",
    "MESSAGE_READY",
    "MESSAGE_STARTED",
    "MESSAGE_TURN_COMPLETE",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "command",
    "decode_line",
    "encode_line",
    "event_from_payload",
    "event_payload",
    "message",
]
