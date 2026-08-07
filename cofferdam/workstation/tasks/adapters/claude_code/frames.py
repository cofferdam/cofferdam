"""Reading Claude Code's stream-json output without trusting it.

The CLI writes newline-delimited JSON on stdout. This module turns that into a
small closed set of normalized records, and it is written on the assumption that
the stream is **untrusted input** — not because the CLI is hostile, but because
a parser that trusts its input is a parser that will one day put an unbounded
dictionary from a subprocess into an API response.

Four properties, each enforced here rather than downstream.

**Bounded per line.** A line longer than :data:`MAX_FRAME_BYTES` is not read
into memory and then discarded — it is truncated as it arrives and the remainder
of the line is drained, so a single pathological frame cannot become a
gigabyte-shaped hole in the daemon.

**Bounded in total.** :data:`MAX_TOTAL_BYTES` caps the whole stream. Past it the
reader stops reading and says so, which turns "a runaway agent filled the disk"
into "the task reports it produced too much output".

**Recognised shapes only.** Every field this module reads is named in source.
An unknown ``type`` becomes an ignored count, never an event; a known type with
an unexpected shape is dropped rather than coerced. Nothing from the CLI's JSON
reaches Task Core as a dictionary — each normalized record is built field by
field out of values that were individually checked.

**Text is text, not markup and not control codes.** Every string that can reach
a screen goes through :func:`sanitize`, which removes ANSI escape sequences and
C0/C1 control characters. The PWA renders with ``textContent``, so this is the
second layer rather than the only one; it exists because "the frontend escapes
it" is a property that survives exactly as long as nobody adds ``innerHTML``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# -- bounds ------------------------------------------------------------------

#: One line. Generous enough for a large assistant message, small enough that a
#: thousand of them are still a bounded amount of memory.
MAX_FRAME_BYTES = 256 * 1024

#: The whole stream, across the life of the task.
MAX_TOTAL_BYTES = 8 * 1024 * 1024

#: How many normalized records are kept for the timeline. Oldest are dropped:
#: the newest activity is what somebody looking at a phone wants.
MAX_RECORDS = 200

#: Bounds on individual normalized strings.
MAX_TEXT_CHARS = 4000
MAX_ACTIVITY_CHARS = 300
MAX_RESULT_CHARS = 16000
MAX_TOOL_NAME_CHARS = 60

# -- the closed vocabulary ---------------------------------------------------
#
# What this parser is willing to say. Every normalized record has one of these
# kinds, and Task Core sees nothing else.

KIND_SESSION_READY = "session_ready"
KIND_ASSISTANT_TEXT = "assistant_text"
KIND_TOOL_ACTIVITY = "tool_activity"
KIND_TOOL_RESULT = "tool_result"
KIND_THINKING_PROGRESS = "thinking_progress"
KIND_RETRY = "retry"
KIND_RATE_LIMIT = "rate_limit"
KIND_TURN_RESULT = "turn_result"

#: Frame ``type`` values this parser recognises. Anything else is counted and
#: ignored — see :attr:`StreamState.ignored_frames`.
KNOWN_FRAME_TYPES = frozenset(
    {"system", "assistant", "user", "result", "rate_limit_event", "stream_event"}
)

#: Tool names are echoed into the activity line, so they are checked against a
#: shape rather than passed through. A tool called ``<img onerror=...>`` is not
#: a tool name, and the right response is to drop it rather than to sanitise it
#: into something that looks legitimate.
_TOOL_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_.-]{0,59}\Z")

#: ANSI/VT escape sequences: OSC, CSI, then the single-character forms.
#:
#: **The order of these alternatives is load-bearing**, and getting it wrong is
#: silent. Python's alternation is first-match, not longest-match, and the
#: single-character class ``[@-Z\\-_]`` contains ``]`` — because ``\\-_`` is the
#: range 0x5C–0x5F, which covers it. With that alternative first, ``ESC ] 0 ;
#: title BEL`` matched only the two-character prefix, the BEL was then removed
#: as a control character, and ``0;title`` was left sitting in the text looking
#: like something Claude had said. A test caught it; the fix is to try the
#: longer forms first.
_ANSI = re.compile(
    r"\x1B(?:\][^\x07\x1B]*(?:\x07|\x1B\\)?|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])"
)


def sanitize(value: Any, limit: int = MAX_TEXT_CHARS) -> Optional[str]:
    """Make one string safe to store and to show, or return ``None``.

    Order matters: escapes are removed *before* control characters, because an
    ANSI sequence is partly made of printable characters and stripping the
    control byte first would leave ``[31m`` behind as visible garbage.

    Newlines and tabs survive — an assistant message has paragraphs, and
    flattening them would make the result unreadable to save nothing.
    """
    if not isinstance(value, str) or not value:
        return None
    text = _ANSI.sub("", value)
    text = "".join(
        character
        for character in text
        if character in "\n\t"
        or not (
            ord(character) < 0x20
            or ord(character) == 0x7F
            or 0x80 <= ord(character) <= 0x9F
            # Bidirectional overrides: invisible, and able to make stored text
            # display in an order it was not written in.
            or 0x202A <= ord(character) <= 0x202E
            or 0x2066 <= ord(character) <= 0x2069
        )
    )
    text = unicodedata.normalize("NFC", text).strip()
    if not text:
        return None
    return text[:limit]


def _one_line(value: Any, limit: int = MAX_ACTIVITY_CHARS) -> Optional[str]:
    text = sanitize(value, limit)
    if text is None:
        return None
    collapsed = " ".join(text.split())
    return collapsed[:limit] or None


@dataclass(frozen=True)
class StreamRecord:
    """One normalized thing that happened, built field by field.

    There is no ``payload`` attribute and no ``raw`` attribute, deliberately.
    A record cannot carry a CLI dictionary because there is nowhere to put one.
    """

    kind: str
    text: Optional[str] = None
    detail: Optional[str] = None
    tool: Optional[str] = None
    is_error: bool = False


@dataclass
class TurnResult:
    """The normalized ``result`` frame — the only thing that ends a turn."""

    is_error: bool
    subtype: Optional[str]
    text: Optional[str]
    session_id: Optional[str]
    stop_reason: Optional[str] = None
    permission_denials: Tuple[str, ...] = ()


@dataclass
class StreamState:
    """Everything the reader has learned, bounded.

    Held by the reader thread and read by :meth:`ClaudeCodeAdapter.inspect`
    under a lock. It is a summary, not a transcript: records are capped, text is
    capped, and the final result is the only thing kept at full length.
    """

    session_id: Optional[str] = None
    records: List[StreamRecord] = field(default_factory=list)
    latest_activity: Optional[str] = None
    latest_output: Optional[str] = None
    turns: int = 0
    last_result: Optional[TurnResult] = None
    ignored_frames: int = 0
    oversized_frames: int = 0
    malformed_frames: int = 0
    bytes_seen: int = 0
    truncated: bool = False

    def add(self, record: StreamRecord) -> None:
        self.records.append(record)
        if len(self.records) > MAX_RECORDS:
            del self.records[: len(self.records) - MAX_RECORDS]


def parse_frame(line: str, state: StreamState) -> List[StreamRecord]:
    """Turn one stream line into zero or more normalized records.

    Pure: it reads ``state`` for the session id and updates the counters, and it
    has no side effect outside the object it was handed. That is what makes the
    whole parser testable without a subprocess.
    """
    text = line.strip()
    if not text:
        return []
    try:
        frame = json.loads(text)
    except ValueError:
        state.malformed_frames += 1
        return []
    if not isinstance(frame, dict):
        state.malformed_frames += 1
        return []

    kind = frame.get("type")
    if kind not in KNOWN_FRAME_TYPES:
        state.ignored_frames += 1
        return []

    if kind == "system":
        return _system(frame, state)
    if kind == "assistant":
        return _assistant(frame)
    if kind == "user":
        return _user(frame)
    if kind == "result":
        return _result(frame, state)
    if kind == "rate_limit_event":
        # Counted, not narrated. The detail carries reset timestamps and quota
        # figures that are nobody's business on a task screen.
        return [StreamRecord(kind=KIND_RATE_LIMIT, text="Waiting on Claude rate limits.")]
    # ``stream_event`` arrives only with --include-partial-messages, which this
    # adapter never passes. Counted rather than parsed, so that turning the flag
    # on by accident shows up as a number instead of a flood.
    state.ignored_frames += 1
    return []


def _system(frame: Dict[str, Any], state: StreamState) -> List[StreamRecord]:
    subtype = frame.get("subtype")
    if subtype == "init":
        # Re-emitted at the start of every turn, not once per process. The
        # session id is read the first time and *verified* thereafter: a second
        # init carrying a different id would mean the process is no longer the
        # session Cofferdam launched, which is a fact worth recording rather
        # than overwriting.
        session = frame.get("session_id")
        if isinstance(session, str) and session:
            if state.session_id is None:
                state.session_id = session
            elif state.session_id != session:
                return [
                    StreamRecord(
                        kind=KIND_SESSION_READY,
                        text="The Claude session identity changed unexpectedly.",
                        is_error=True,
                    )
                ]
        return [StreamRecord(kind=KIND_SESSION_READY, text="Claude session ready.")]
    if subtype == "thinking_tokens":
        # A liveness signal only. No thinking content is requested, parsed or
        # kept — the estimate says the task is working, and that is all.
        return [StreamRecord(kind=KIND_THINKING_PROGRESS, text="Claude is working.")]
    if subtype == "api_retry":
        attempt = frame.get("attempt")
        total = frame.get("max_retries")
        detail = None
        if isinstance(attempt, int) and isinstance(total, int) and 0 < attempt <= 100:
            detail = "attempt " + str(attempt) + " of " + str(total)
        return [
            StreamRecord(
                kind=KIND_RETRY, text="Retrying a Claude API request.", detail=detail
            )
        ]
    state.ignored_frames += 1
    return []


def _content_blocks(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    message = frame.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    # Bounded: a message with ten thousand blocks is not something to iterate.
    return [block for block in content[:64] if isinstance(block, dict)]


def _assistant(frame: Dict[str, Any]) -> List[StreamRecord]:
    records: List[StreamRecord] = []
    for block in _content_blocks(frame):
        block_type = block.get("type")
        if block_type == "text":
            text = sanitize(block.get("text"))
            if text:
                records.append(StreamRecord(kind=KIND_ASSISTANT_TEXT, text=text))
        elif block_type == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and _TOOL_NAME.match(name):
                records.append(
                    StreamRecord(
                        kind=KIND_TOOL_ACTIVITY,
                        text="Claude used " + name + ".",
                        tool=name,
                    )
                )
            else:
                records.append(
                    StreamRecord(kind=KIND_TOOL_ACTIVITY, text="Claude used a tool.")
                )
        # `thinking` and `redacted_thinking` blocks are skipped without comment.
        # Claude's private reasoning is not Cofferdam's to collect, store or
        # display, and the absence of a branch here is the implementation of
        # that.
    return records


def _user(frame: Dict[str, Any]) -> List[StreamRecord]:
    """Tool results come back as ``user`` frames. Report the outcome, not the body.

    A tool result can be an entire file. Putting it in the task timeline would
    turn the default view into the terminal this milestone refuses to build, so
    only the shape is recorded: something came back, and whether it was an
    error.
    """
    records: List[StreamRecord] = []
    for block in _content_blocks(frame):
        if block.get("type") != "tool_result":
            continue
        is_error = block.get("is_error") is True
        records.append(
            StreamRecord(
                kind=KIND_TOOL_RESULT,
                text="A tool reported an error." if is_error else "A tool finished.",
                is_error=is_error,
            )
        )
    return records


def _permission_denials(value: Any) -> Tuple[str, ...]:
    """Tool names from ``permission_denials``, and nothing else from it.

    The entries carry the tool input as well — file paths, command strings,
    whatever Claude was about to do. That is exactly the material that must not
    be published to a phone unexamined, so only a well-formed tool name is
    taken out of each entry.
    """
    if not isinstance(value, list):
        return ()
    names: List[str] = []
    for entry in value[:16]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("tool_name") or entry.get("tool")
        if isinstance(name, str) and _TOOL_NAME.match(name) and name not in names:
            names.append(name)
    return tuple(names)


def _result(frame: Dict[str, Any], state: StreamState) -> List[StreamRecord]:
    subtype = frame.get("subtype")
    # `is_error` is authoritative, and a missing one is treated as an error
    # rather than as success. An absent field must never be the reason a task
    # is reported complete.
    is_error = frame.get("is_error") is not False
    session = frame.get("session_id")
    result_text = sanitize(frame.get("result"), MAX_RESULT_CHARS)
    stop_reason = _one_line(frame.get("stop_reason"), 60)
    denials = _permission_denials(frame.get("permission_denials"))

    state.turns += 1
    state.last_result = TurnResult(
        is_error=is_error,
        subtype=subtype if isinstance(subtype, str) and len(subtype) <= 60 else None,
        text=result_text,
        session_id=session if isinstance(session, str) else None,
        stop_reason=stop_reason,
        permission_denials=denials,
    )
    return [
        StreamRecord(
            kind=KIND_TURN_RESULT,
            text=result_text,
            detail=state.last_result.subtype,
            is_error=is_error,
        )
    ]


__all__ = [
    "KIND_ASSISTANT_TEXT",
    "KIND_RATE_LIMIT",
    "KIND_RETRY",
    "KIND_SESSION_READY",
    "KIND_THINKING_PROGRESS",
    "KIND_TOOL_ACTIVITY",
    "KIND_TOOL_RESULT",
    "KIND_TURN_RESULT",
    "KNOWN_FRAME_TYPES",
    "MAX_FRAME_BYTES",
    "MAX_RECORDS",
    "MAX_TOTAL_BYTES",
    "StreamRecord",
    "StreamState",
    "TurnResult",
    "parse_frame",
    "sanitize",
]
