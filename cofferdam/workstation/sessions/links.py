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

**``LINK_PATTERN`` is now written against real output.** M2H PR2 could not
confirm it: the CLI stopped at its own one-time consent question, waiting for a
``stdin`` this package deliberately does not give it, so no session and no URL
ever existed. M2H PR2.5 closed that by having a person answer the prompt once at
the machine — Cofferdam still cannot and does not answer it — after which two
independent bounded PTY startups produced the same link shape.

The confirmed structure, and nothing wider:

* ``https``, never ``http``
* an allowlisted host (:data:`ALLOWED_LINK_HOSTS`)
* the exact path :data:`LINK_PATH`, one segment, no session id in the path
* exactly one query parameter, :data:`LINK_QUERY_KEY`
* whose value is the capability: URL-safe base64 characters, 28 of them in both
  observations, bounded here rather than pinned so that a token-length change
  upstream does not turn a working link into "no link available"

The capability therefore lives in the **query string**, which is the part of
this module that the rest of the package is built around: a URL whose secret is
in the query is one that leaks through a referrer header, a proxy log, an
analytics tag or a screenshot of an address bar. Nothing here writes it anywhere
but the 0600 runtime state file, and :func:`redact` removes it from everything
else.

Restarting is **not** revocation
--------------------------------

Worth stating plainly, because M2H PR2 assumed the opposite. The parameter is
called ``environment`` and it means it: it identifies the registered
environment, not one launch of it. Two Remote Control generations started
minutes apart in the same project produced the *same* URL, and the CLI says so
on shutdown — "Environment preserved. Restart `claude remote-control` to
reconnect existing sessions."

So a person who has seen this URL keeps a working capability across a Cofferdam
stop and start. Cofferdam's generation rules still do what they were built to
do — a stale generation cannot read the current state, and the stored link is
cleared on stop, so *Cofferdam* stops handing it out — but nobody should read
that as the link being invalidated. Revoking it is an account-level action on
Anthropic's side, and this package has no mechanism for it and does not pretend
to.

The pattern is narrow on purpose. It was previously "any path on an allowlisted
host", which would have matched a docs link, a login page or an error page the
child happened to print, and stored that as a session capability.

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
#:
#: **This is an operational gate, not a note.** While it is ``False``,
#: :func:`find_link` returns nothing at all, so no candidate URL can be
#: persisted, returned or announced anywhere in the system. The failure mode it
#: closes is the dangerous one: a pattern nobody has checked against real output
#: matching something *else* the child printed, and Cofferdam then storing that
#: and handing it to a phone as a session link.
#:
#: Fail-closed was chosen over fail-open deliberately. "No link available" is an
#: inconvenience somebody can act on; "here is a link" that is actually some
#: other URL the process mentioned is a capability handed to whoever holds it.
#:
#: Flipped to ``True`` only in a commit that also records a real observation and
#: corrects :data:`LINK_PATTERN` to match it. **M2H PR2.5 is that commit:** the
#: consent prompt was answered once by a person at the machine, and two bounded
#: PTY startups then produced the same structure, which is what
#: :data:`LINK_PATTERN` below now encodes.
#:
#: It remains a gate, not a note. Setting it back to ``False`` — on a workstation
#: where the format is in doubt, or in a build that has not re-confirmed it —
#: still stops every link from being recognised, persisted or returned, and a
#: test asserts that closed behaviour has not rotted.
LINK_FORMAT_CONFIRMED = True

#: Bounded: a session URL is not a document. Anything longer is not a link.
MAX_LINK_CHARS = 512

#: The one path a Remote Control link uses. Confirmed, and exact: the session is
#: identified entirely by the query parameter below, so a link with anything else
#: after the host is not one of these.
LINK_PATH = "/code"

#: The one query parameter, whose value is the capability itself.
LINK_QUERY_KEY = "environment"

#: Bounds on that value. Both live observations produced 28 URL-safe base64
#: characters; the band is wider so an upstream length change degrades into
#: "still recognised" rather than "no link available", while a stray
#: ``?environment=1`` is still refused.
#:
#: The lower bound is the anti-truncation defence *inside* the pattern. The
#: primary one is :class:`LinkScanner`, which only ever scans complete lines, so
#: a URL split across two terminal reads is never matched in halves.
LINK_TOKEN_MIN_CHARS = 16
LINK_TOKEN_MAX_CHARS = 128

#: Any absolute https URL. Used for *redaction*, where over-matching is safe and
#: under-matching is a leak.
_ANY_URL = re.compile(r"https?://[^\s<>\"')\]]+")

#: The confirmed Remote Control link, and only that.
#:
#: Every part is pinned to what was observed: scheme, an allowlisted host, the
#: exact path, the single query key, and a URL-safe token. The trailing
#: assertion matters — without it the pattern would happily match the first 16
#: characters of a longer token and hand out a capability URL that fails
#: somewhere far away from the mistake.
LINK_PATTERN = re.compile(
    r"https://(?:"
    + "|".join(re.escape(host) for host in ALLOWED_LINK_HOSTS)
    + r")"
    + re.escape(LINK_PATH)
    + r"\?"
    + re.escape(LINK_QUERY_KEY)
    + r"=[A-Za-z0-9_-]{"
    + str(LINK_TOKEN_MIN_CHARS)
    + r","
    + str(LINK_TOKEN_MAX_CHARS)
    + r"}(?![A-Za-z0-9_-])"
)

#: What replaces a URL in anything that can be logged.
REDACTION = "[remote-control-link redacted]"

#: ANSI escape sequences: CSI (colour, cursor movement), OSC (window title and
#: hyperlinks), and single-character escapes.
#:
#: Needed because the child now writes to a pseudo-terminal. A TTY-aware program
#: paints its output — colour codes, cursor jumps, line rewrites — and a URL with
#: a colour reset spliced into the middle of it does not match any pattern. OSC 8
#: matters most: a terminal hyperlink wraps the URL in an escape sequence, so the
#: link is *there* and invisible to a naive search.
_ANSI = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b\[[0-9;?]*[ -/]*[@-~]"              # CSI
    r"|\x1b[@-Z\\-_]"                          # single-character escapes
)


#: The payload of an OSC 8 terminal hyperlink: ``ESC ] 8 ; params ; URI ST``.
#:
#: Read *before* stripping, because in a hyperlink the URI exists only inside
#: the escape sequence — the visible text is a label like "open session". Strip
#: first and the link is gone; ignore OSC 8 entirely and a terminal-aware child
#: appears to print no URL at all.
_OSC8 = re.compile(r"\x1b\]8;[^;\x07\x1b]*;([^\x07\x1b]*)(?:\x07|\x1b\\)")


def hyperlink_targets(text: str) -> List[str]:
    """Every URI carried in an OSC 8 hyperlink in that text."""
    return [target for target in _OSC8.findall(text) if target]


def strip_ansi(text: str) -> str:
    """Remove terminal control sequences before anything is matched.

    Applied to PTY output on the way in. A link is only recognisable once the
    paint is off it, and an OSC 8 hyperlink hides one completely otherwise.
    """
    return _ANSI.sub("", text)


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
    if not LINK_FORMAT_CONFIRMED:
        # The gate. Nothing is recognised, so nothing downstream can persist,
        # return or announce a link while the format is unverified.
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return None
    candidate = _first_candidate(text)
    if candidate is None or len(candidate) > MAX_LINK_CHARS:
        return None
    return candidate


def _first_candidate(text: str) -> Optional[str]:
    """A pattern match from the hyperlink targets first, then the visible text."""
    for target in hyperlink_targets(text):
        match = LINK_PATTERN.fullmatch(target) or LINK_PATTERN.search(target)
        if match is not None:
            return match.group(0)
    match = LINK_PATTERN.search(strip_ansi(text))
    return match.group(0) if match is not None else None


def match_link_format(text: object) -> Optional[str]:
    """What :func:`find_link` *would* return if the format were confirmed.

    Exists only so the live spike and its tests can exercise the pattern without
    the gate silently answering for it. **No production path calls this** — a
    test asserts that — because a second way to get a link past the gate would
    be the gate not existing.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not isinstance(text, str):
        return None
    candidate = _first_candidate(text)
    return candidate if candidate is not None and len(candidate) <= MAX_LINK_CHARS else None


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
    "LINK_PATH",
    "LINK_QUERY_KEY",
    "LINK_TOKEN_MAX_CHARS",
    "LINK_TOKEN_MIN_CHARS",
    "ALLOWED_LINK_HOSTS",
    "LINK_FORMAT_CONFIRMED",
    "LINK_PATTERN",
    "MAX_LINK_CHARS",
    "REDACTION",
    "LinkScanner",
    "contains_link",
    "hyperlink_targets",
    "find_link",
    "match_link_format",
    "strip_ansi",
    "redact",
    "redact_lines",
]
