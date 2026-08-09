"""Reading a question tool's input without believing anything unproven about it.

The Claude Agent SDK distribution contains **no ``AskUserQuestion`` type and no
schema for one.** That was checked rather than assumed: the string does not
appear anywhere in the published ``claude-agent-sdk`` 0.2.134 source archive.
The tool belongs to the CLI, it arrives as an ordinary ``ToolUseBlock`` name and
an ordinary permission request, and its input shape is whatever the CLI happens
to send.

So this module is written to a rule that is unusual for a parser: **it is allowed
to fail to understand.** A shape it cannot read conservatively becomes bounded
*activity* — "the agent asked something Cofferdam has no words for" — and never a
fabricated question with invented text. Those two mistakes are not the same size.
A missed clarification costs a task that keeps running with an activity line in
its history. An invented one shows somebody a question the agent never asked and
then sends their answer to a model as though it had.

Two functions, and the split is the point
-----------------------------------------

:func:`observe` records *that* something arrived and what shape it had — key
names, value type names, counts. It never records a value. It is what makes a
live spike able to establish the schema without a single character of provider
content or user content being written down.

:func:`read_question` is the conservative reader. It produces a normalized
question only from shapes this build can defend, and ``None`` for everything
else.

What is never kept
------------------

No raw payload, no dictionary, no nested object, no free-form value. The
observation carries key *names* and type *names*; the normalized question carries
sanitized, bounded, length-limited text that has been through the same
:func:`~....delegated.safe_text` every other provider string passes.

Option identifiers are **Cofferdam's**, generated from position. A provider
string used as an identifier would be a provider deciding what an answer route's
primary key looks like, and the answer submission surface is the last place that
should be true.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...delegated import (
    ANSWER_MODE_FREE_TEXT,
    ANSWER_MODE_MULTIPLE_CHOICE,
    ANSWER_MODE_SINGLE_CHOICE,
    ANSWER_MODE_UNKNOWN,
    MAX_OPTIONS,
    MAX_OPTION_LABEL_CHARS,
    MAX_QUESTION_CHARS,
    safe_line,
    safe_text,
)

#: Tool names that mean "the agent is asking a person something" rather than "the
#: agent is doing something". Code-owned: this tuple is the definition, not a
#: guess refined at runtime.
QUESTION_TOOL_NAMES: Tuple[str, ...] = ("AskUserQuestion",)

#: Whether the input schema behind :data:`QUESTION_TOOL_NAMES` has been observed
#: against a real provider session.
#:
#: ``False`` in this build, and it is load-bearing rather than decorative: the
#: adapter reports it in its capability description, the documentation quotes it,
#: and a clarification produced while it is ``False`` is marked as coming from an
#: unverified schema. A live spike flips it, and flipping it without one would be
#: the single most dishonest edit somebody could make to this package.
SCHEMA_VERIFIED = False

#: What a live spike must establish before :data:`SCHEMA_VERIFIED` may change.
#: Written as data so the spike has a checklist and the pull request has a table.
SCHEMA_EVIDENCE_REQUIRED: Tuple[str, ...] = (
    "the exact tool name",
    "the exact bounded top-level input shape",
    "whether more than one question can arrive at once",
    "which field carries the question text",
    "which fields carry an option label, value and description",
    "whether a free-text answer is accepted",
    "how the session resumes after the tool callback returns",
    "whether the provider session identifier is unchanged afterwards",
)

# -- answer modes ------------------------------------------------------------
#
# Imported, not defined. The vocabulary is provider-neutral and belongs to Task
# Core: it is part of what a *client* is told about a pending question, and no
# client should have to know which provider asked. Re-declaring the four strings
# here would be two sources for one closed set, which is how a fifth mode ends up
# meaning different things on either side of the boundary.

# -- bounds ------------------------------------------------------------------
#
# Separate from Task Core's own bounds and smaller, for the reason recorded in
# `delegated.py`: a task result is something a person asked for and will read,
# while a provider's question is something a provider volunteered.

#: The most questions one tool input may contain. A provider that sent forty is
#: a provider whose input this build refuses rather than truncates halfway into.
MAX_QUESTIONS = 4

#: How many top-level keys of an unknown input are described in an observation.
MAX_OBSERVED_KEYS = 24

#: How long an option's description may be, when a verified schema turns out to
#: have one. Shorter than a question, because it is a subtitle on a button.
MAX_OPTION_DESCRIPTION_CHARS = 200

#: Cofferdam's own option identifier shape. Positional, so it is stable for one
#: question and meaningless outside it, and short enough to sit in a request body
#: without anybody being tempted to read meaning into it.
OPTION_ID_PREFIX = "opt"


@dataclass(frozen=True)
class ObservedOption:
    """One choice, as this build is willing to store it."""

    option_id: str
    label: str
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "option_id": self.option_id,
            "label": self.label,
            "description": self.description,
        }


@dataclass(frozen=True)
class ObservedQuestion:
    """One question, normalized, bounded, and honest about its provenance.

    ``schema_verified`` travels with the question rather than being looked up,
    because a question stored today keeps the truth about what was known when it
    was stored. A later build that verifies the schema must not retroactively
    claim that yesterday's records were verified.
    """

    question: str
    answer_mode: str
    options: Tuple[ObservedOption, ...] = ()
    schema_verified: bool = False

    @property
    def allows_free_text(self) -> bool:
        return self.answer_mode in (ANSWER_MODE_FREE_TEXT, ANSWER_MODE_UNKNOWN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer_mode": self.answer_mode,
            "options": [option.to_dict() for option in self.options],
            "schema_verified": self.schema_verified,
        }


@dataclass(frozen=True)
class ToolInputObservation:
    """The shape of one tool input, with none of its content.

    Every field here is a name, a type name or a count. There is no field that
    can hold a value, which is what lets this be produced during a live spike and
    written into a pull request without anybody having to redact it afterwards.
    """

    tool_name: Optional[str]
    key_names: Tuple[str, ...] = ()
    value_types: Tuple[str, ...] = ()
    question_count: int = 0
    option_count: int = 0
    readable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "key_names": list(self.key_names),
            "value_types": list(self.value_types),
            "question_count": self.question_count,
            "option_count": self.option_count,
            "readable": self.readable,
        }

    def summary(self) -> str:
        """One bounded line for a task's history. Names and counts only."""
        keys = ", ".join(self.key_names[:8]) or "none"
        return (
            "The agent asked a question Cofferdam could not read (fields: "
            + keys
            + ")."
        )


def is_question_tool(tool_name: Any) -> bool:
    return isinstance(tool_name, str) and tool_name in QUESTION_TOOL_NAMES


def observe(tool_name: Any, payload: Any) -> ToolInputObservation:
    """Describe a tool input's shape without keeping any of it.

    Deliberately total: it never raises and it accepts anything, because its
    whole job is to say something useful about the input that the *reader* could
    not understand. A describer that could itself fail on a surprising shape
    would leave the one case it exists for undescribed.
    """
    name = tool_name if isinstance(tool_name, str) and len(tool_name) <= 60 else None
    if not isinstance(payload, dict):
        return ToolInputObservation(
            tool_name=name,
            value_types=(type(payload).__name__,),
            readable=False,
        )

    keys: List[str] = []
    types: List[str] = []
    for key in sorted(payload)[:MAX_OBSERVED_KEYS]:
        if not isinstance(key, str):
            continue
        # The key *name* is bounded and sanitized like any other provider
        # string: an input whose keys were themselves hostile text would
        # otherwise put it into an activity line.
        cleaned = safe_line(key, 60)
        if cleaned is None:
            continue
        keys.append(cleaned)
        types.append(type(payload[key]).__name__)

    questions = _question_entries(payload)
    option_total = 0
    for entry in questions:
        raw_options = entry.get("options") if isinstance(entry, dict) else None
        if isinstance(raw_options, (list, tuple)):
            option_total += len(raw_options)

    return ToolInputObservation(
        tool_name=name,
        key_names=tuple(keys),
        value_types=tuple(types),
        question_count=len(questions),
        option_count=option_total,
        readable=read_question(payload) is not None,
    )


def read_question(payload: Any) -> Optional[ObservedQuestion]:
    """One normalized question, or ``None`` if this build cannot defend one.

    ``None`` is the common and correct answer for anything surprising. The
    caller's contract is that ``None`` becomes bounded activity and never a
    clarification — see :mod:`.normalize`.

    Only the **first** question is read even when several arrive. That is a
    deliberate limit rather than an oversight: one active clarification per
    provider turn is the rule the lifecycle is built on, and a build that stored
    four pending questions it could answer one of would be a build whose task
    could not truthfully leave ``waiting_for_user``.
    """
    entries = _question_entries(payload)
    if not entries:
        return None
    if len(entries) > MAX_QUESTIONS:
        # Refused rather than truncated. An input carrying more questions than
        # this build has ever seen is an input this build does not understand,
        # and reading the first of forty would be reading a shape by luck.
        return None

    first = entries[0]
    question = safe_text(first.get("question"), MAX_QUESTION_CHARS)
    if question is None:
        return None

    options = _read_options(first.get("options"))
    if options is None:
        # Options were present and unusable. Distinct from "no options at all",
        # which is a legitimate free-text question — this is a shape that
        # claimed to offer choices and did not, and answering it with a guess
        # would send the model a choice nobody made.
        return None

    mode = _answer_mode(first, options)
    return ObservedQuestion(
        question=question,
        answer_mode=mode,
        options=options,
        schema_verified=SCHEMA_VERIFIED,
    )


def _question_entries(payload: Any) -> List[Dict[str, Any]]:
    """The question objects in an input, in the two shapes this build accepts.

    The singular form — a top-level ``question`` — and the plural form, a
    ``questions`` list of objects. Both are read the same way afterwards, so
    there is one reader below rather than two that could disagree.
    """
    if not isinstance(payload, dict):
        return []
    plural = payload.get("questions")
    if isinstance(plural, (list, tuple)):
        return [entry for entry in plural if isinstance(entry, dict)]
    if isinstance(payload.get("question"), str):
        return [payload]
    return []


def _read_options(value: Any) -> Optional[Tuple[ObservedOption, ...]]:
    """Bounded options, or ``None`` when a present option list is unusable.

    The three-way return is the whole subtlety. Absent options are an empty
    tuple: a free-text question is a real question. A present list that yields
    nothing usable is ``None``: something offered choices and this build could
    not read them, and presenting that as free text would change what was asked.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        return None
    if not value:
        return ()
    if len(value) > MAX_OPTIONS:
        return None

    options: List[ObservedOption] = []
    for index, entry in enumerate(value):
        if isinstance(entry, str):
            label = safe_line(entry, MAX_OPTION_LABEL_CHARS)
            description = None
        elif isinstance(entry, dict):
            label = safe_line(entry.get("label"), MAX_OPTION_LABEL_CHARS)
            description = safe_line(
                entry.get("description"), MAX_OPTION_DESCRIPTION_CHARS
            )
        else:
            return None
        if label is None:
            return None
        options.append(
            ObservedOption(
                option_id=option_id_for(index),
                label=label,
                description=description,
            )
        )
    return tuple(options)


def option_id_for(index: int) -> str:
    """Cofferdam's identifier for the option at ``index``. Positional, stable.

    Not derived from the label. A label-derived id would change when a provider
    reworded an option, and would put provider text into a request path — two
    problems for no benefit over counting.
    """
    return OPTION_ID_PREFIX + str(int(index) + 1)


def _answer_mode(entry: Dict[str, Any], options: Sequence[ObservedOption]) -> str:
    """Which of the four modes this question is, read conservatively.

    With no options it is free text. With options it is a single choice unless
    something in the input unambiguously says otherwise — and "unambiguously"
    means a boolean that is exactly ``True``, not a truthy value, because a
    provider sending the string ``"false"`` must not be read as multi-select.

    While :data:`SCHEMA_VERIFIED` is ``False`` a question carrying options that
    this build read but cannot vouch for is still reported as a choice: the
    options themselves were read successfully, and the uncertainty is recorded
    on the question rather than by pretending it had no options.
    """
    if not options:
        return ANSWER_MODE_FREE_TEXT
    for name in ("multiSelect", "multi_select", "allow_multiple"):
        if entry.get(name) is True:
            return ANSWER_MODE_MULTIPLE_CHOICE
    return ANSWER_MODE_SINGLE_CHOICE


__all__ = [
    "MAX_OBSERVED_KEYS",
    "MAX_OPTION_DESCRIPTION_CHARS",
    "MAX_QUESTIONS",
    "OPTION_ID_PREFIX",
    "QUESTION_TOOL_NAMES",
    "SCHEMA_EVIDENCE_REQUIRED",
    "SCHEMA_VERIFIED",
    "ObservedOption",
    "ObservedQuestion",
    "ToolInputObservation",
    "is_question_tool",
    "observe",
    "option_id_for",
    "read_question",
]
