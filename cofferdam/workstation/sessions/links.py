"""Recognising a Remote Control link, and making sure it never leaks.

A Remote Control URL is **capability material**. Anyone holding it can reach an
interactive Claude session running inside a registered project on this
workstation — it is closer to a bearer token than to a status field, and the
whole of this module exists to treat it that way.

The consequence is that the URL travels one path and one only: the child's
stdout, into :mod:`.state` (owner-only, atomic), out through one authenticated
retrieval route. It is not in ordinary status responses, not in audit records,
not in exceptions, and not in journald. :func:`redact` is applied to every line
of child output *before* that line can be logged, so the redaction is a property
of the pipeline rather than a rule each call site has to remember.

On the recogniser
-----------------

``LINK_PATTERN`` is written against the documented behaviour of
``claude remote-control`` — sessions are controlled from ``claude.ai/code`` —
and is deliberately narrow: an ``https`` URL on an allowlisted host only.

**It has not yet been confirmed against real output.** The M2H PR2 live spike
observes one bounded startup and this pattern is corrected from what the process
actually prints. Until then :data:`LINK_FORMAT_CONFIRMED` is ``False`` and the
supervisor reports "no link captured" rather than pretending. Guessing here
would be the worst kind of wrong: a pattern that half-matches would store a
truncated capability URL and report success.

Redaction is deliberately **wider** than recognition. It removes any ``https``
URL from retained output, not only ones matching the pattern, because a
redactor that only hides what it already understands is a redactor that leaks
the first time the format changes.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

#: Hosts a Remote Control link may live on. An allowlist, not a pattern: a URL
#: printed by the child pointing anywhere else is not a session link, and
#: storing it would mean storing whatever a compromised or updated child chose
#: to print.
ALLOWED_LINK_HOSTS: Tuple[str, ...] = ("claude.ai", "www.claude.ai")

#: Whether :data:`LINK_PATTERN` has been confirmed against real process output.
#: Flipped to ``True`` in the commit that records the live observation, together
#: with the pattern correction. See the module docstring.
LINK_FORMAT_CONFIRMED = False

#: Bounded: a session URL is not a document. Anything longer is not a link.
MAX_LINK_CHARS = 512

#: Any absolute https URL. Used for *redaction*, where over-matching is safe and
#: under-matching is a leak.
_ANY_URL = re.compile(r"https?://[^\s<>\"')\]]+")

#: A candidate Remote Control link: https, an allowlisted host, then a run of
#: non-delimiter characters.
#:
#: Deliberately **unbounded** in the pattern, with the length checked after the
#: match instead. A bounded quantifier here would silently *truncate* a longer
#: URL into something that still looks like a link — and a truncated capability
#: URL that is stored and handed out is worse than no URL at all, because it
#: fails somewhere far away from the mistake.
LINK_PATTERN = re.compile(
    r"https://(?:" + "|".join(re.escape(host) for host in ALLOWED_LINK_HOSTS) + r")/[^\s<>\"')\]]+"
)

#: What replaces a URL in anything that can be logged.
REDACTION = "[remote-control-link redacted]"


def redact(text: object) -> str:
    """Every URL in that text replaced, bounded, single-line, control-free.

    Applied to child output before it reaches journald, to error details before
    they reach a status object, and to anything that could reach an audit
    record. Over-matches on purpose: it removes ``http`` and ``https`` URLs
    regardless of host, so a link on a host this build does not recognise is
    still not printed.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return ""
    cleaned = _ANY_URL.sub(REDACTION, text)
    cleaned = "".join(
        character
        for character in cleaned
        if not (ord(character) < 0x20 or ord(character) == 0x7F)
    )
    return " ".join(cleaned.split())


def contains_link(text: object) -> bool:
    """Whether that text carries something that looks like any URL at all."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return isinstance(text, str) and bool(_ANY_URL.search(text))


def find_link(text: object) -> Optional[str]:
    """The first complete Remote Control link in that text, or ``None``.

    "Complete" is the whole point. This is only ever called on text that has
    already been split at a newline, so a match cannot be the front half of a
    URL whose tail has not arrived yet — see :class:`LinkScanner`, which is what
    guarantees that precondition.

    A candidate longer than :data:`MAX_LINK_CHARS` is rejected outright rather
    than trimmed. Trimming would produce a plausible-looking link that does not
    work, and "no link" is a far better answer than "a link that fails later".
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return None
    match = LINK_PATTERN.search(text)
    if match is None:
        return None
    candidate = match.group(0)
    if len(candidate) > MAX_LINK_CHARS:
        return None
    return candidate


class LinkScanner:
    """Finds a link in a stream that arrives in arbitrary chunks.

    A pipe does not respect line boundaries: a URL can be split across two
    reads. The naive fix — searching each chunk, or a sliding window — has a
    subtle and dangerous failure, which is why this class exists in this shape.

    ``https://claude.ai/code/`` is *itself* a valid match for any sane URL
    pattern. So a chunk ending mid-URL yields a match that looks complete, and a
    scanner that accepted it would store a **truncated capability URL** and
    report success. The stored link would then fail for whoever opened it, far
    away from the code that made the mistake.

    So nothing is matched until a newline proves the line is finished. Chunks
    accumulate in a bounded buffer, complete lines are scanned, and a trailing
    partial line waits for the rest. An unbounded accumulator would be a
    memory-exhaustion bug on a process that may run for hours, so the buffer is
    capped — and a "line" longer than the cap cannot contain a valid link
    anyway, since :data:`MAX_LINK_CHARS` is far smaller.
    """

    #: Enough for any line that could hold a link, with room to spare.
    MAX_BUFFER = MAX_LINK_CHARS * 8

    def __init__(self) -> None:
        self._buffer = ""
        self.link: Optional[str] = None

    def feed(self, chunk: object) -> Optional[str]:
        """Add a chunk; return the link the first time a complete one is seen.

        Only the **first** link is kept. A later one does not overwrite it: a
        second URL in the same generation is either the same session restated or
        something this build does not understand, and silently replacing a live
        capability reference with an unexplained one is not an improvement.
        """
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", "replace")
        if not isinstance(chunk, str) or not chunk:
            return None

        self._buffer += chunk
        found = None

        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            candidate = find_link(line)
            if candidate is not None and self.link is None:
                self.link = candidate
                found = candidate

        if len(self._buffer) > self.MAX_BUFFER:
            # A line this long cannot hold a link. Drop it rather than grow.
            self._buffer = self._buffer[-MAX_LINK_CHARS:]

        return found

    def finish(self) -> Optional[str]:
        """Scan whatever is left when the stream ends without a final newline."""
        if self.link is not None or not self._buffer:
            return None
        candidate = find_link(self._buffer)
        self._buffer = ""
        if candidate is not None:
            self.link = candidate
        return candidate


def redact_lines(lines: List[object], limit: int) -> List[str]:
    """A bounded, redacted tail of retained output.

    Used for the operational error summary. Bounded twice — line count and line
    length — because the point of retaining any output at all is to answer "why
    did it not start", and that answer is never a thousand lines long.
    """
    tail = [redact(line) for line in lines[-limit:]]
    return [line[:200] for line in tail if line]


__all__ = [
    "ALLOWED_LINK_HOSTS",
    "LINK_FORMAT_CONFIRMED",
    "LINK_PATTERN",
    "MAX_LINK_CHARS",
    "REDACTION",
    "LinkScanner",
    "contains_link",
    "find_link",
    "redact",
    "redact_lines",
]
