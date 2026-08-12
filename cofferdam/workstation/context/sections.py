"""A small heading-aware reader, so a long document does not eat the budget.

`DECISIONS.md` is over a hundred kilobytes. Putting it in a pack whole is not an
option, and putting its first few kilobytes in is a decision about *which* few —
so this module exists to make that decision visible, bounded and repeatable.

What it is
----------

CommonMark ATX headings, and nothing else. It splits text into sections, gives
each a stable identity, and returns them in document order. It does not parse
Markdown, does not resolve links, does not rewrite anything, and has no notion
of a file: the input is a string somebody else already read through
:class:`~..mind.service.MindService`.

What it deliberately is not
---------------------------

**Not relevance.** A section chosen here was chosen because of where it sits in
the document, and the pack labels it ``structural`` for exactly that reason.
Semantic candidate retrieval is M2N and it is not approximated here — a keyword
heuristic wearing the word "relevant" would be the most expensive kind of wrong,
because everything downstream would believe it.

**Not a link follower.** `[[wikilinks]]` are ordinary characters. Obsidian's
vault conventions are content to this module, `.obsidian/` is never reached
because nothing here reaches anything, and there is no traversal of any kind.
Backlinks are M2N too.

Section identity
----------------

A ``section_id`` is a slug of the heading, restricted to ``[a-z0-9-]``. That
restriction is doing real work: it is what makes a heading like
``## ../../etc/passwd`` unable to put a path-shaped string into a pack's
provenance, whatever the document says. Diacritics are folded rather than
dropped (``## Görüşler`` → ``gorusler``) so Turkish headings stay addressable;
a heading that folds to nothing at all falls back to its position.

Repeated headings — which a real `DECISIONS.md` has — get ``-2``, ``-3`` and so
on, so the second ``## Notes`` is addressable and is never silently the first.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: Bounded because a heading is echoed into provenance and rendered. Long enough
#: for every heading in Cofferdam's own documents, short enough that a document
#: cannot smuggle a paragraph into a section reference.
MAX_HEADING_CHARS = 120

#: ATX only, up to three leading spaces, as CommonMark specifies. Setext
#: headings are deliberately unsupported: none of Cofferdam's documents use
#: them, and guessing that an underlined line was a heading is the kind of
#: cleverness that makes section identity unstable.
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")

#: A fence is three or more backticks or tildes. Headings inside one are code,
#: not structure — every one of Cofferdam's documents contains fenced JSON with
#: comment lines in it, and treating those as sections would shred the outline.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

_CLOSING_HASHES = re.compile(r"[ \t]+#+[ \t]*$")

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Section:
    """One heading and everything under it, until the next heading.

    ``heading`` is ``None`` for the text before the first heading, which is kept
    rather than discarded: in `STATUS.md` and `DESIGN.md` that preamble is the
    part a reader would actually want first.

    ``body`` includes the heading line, so a section put into a pack still reads
    as the document it came from.
    """

    heading: Optional[str]
    level: int
    section_id: str
    ordinal: int
    body: str


def slugify(value: str) -> str:
    """A heading's stable identity: lowercase, ASCII, ``[a-z0-9-]`` or empty.

    Folding happens through NFKD so that accented Latin text keeps its letters
    (``ö`` → ``o``) instead of collapsing to separators. Everything that is not
    an ASCII letter or digit afterwards — punctuation, control characters,
    separators, scripts that do not decompose to ASCII — becomes a single dash,
    and leading and trailing dashes are removed.
    """
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    slug = _NOT_SLUG.sub("-", folded.lower()).strip("-")
    return slug[:MAX_HEADING_CHARS].strip("-")


def _bounded(heading: str) -> str:
    cleaned = "".join(
        " " if character in "\t\r\n" else character
        for character in heading
        if unicodedata.category(character) not in ("Cc", "Cf")
    )
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_HEADING_CHARS]


def split_sections(text: str) -> Tuple[Section, ...]:
    """Split Markdown into sections in document order.

    One pass, no lookahead, no recursion. A section runs from its heading to the
    next heading of *any* level: nesting is not modelled, because the only thing
    the builder needs is a bounded, addressable, in-order list, and a tree would
    add a second way to be wrong about where a section ends.
    """
    lines = text.splitlines(keepends=True)
    marks: List[Tuple[int, int, Optional[str]]] = []
    fence: Optional[str] = None

    for index, line in enumerate(lines):
        fenced = _FENCE.match(line)
        if fenced is not None:
            marker = fenced.group(1)
            if fence is None:
                fence = marker[0] * 3
                continue
            if marker[0] * 3 == fence and not fenced.group(2).strip():
                fence = None
            continue
        if fence is not None:
            continue
        heading = _HEADING.match(line)
        if heading is not None:
            title = _CLOSING_HASHES.sub("", heading.group(2) or "").strip()
            marks.append((index, len(heading.group(1)), title))

    if not marks:
        return _finish([(None, 0, text)]) if text.strip() else ()

    raw: List[Tuple[Optional[str], int, str]] = []
    if marks[0][0] > 0:
        preamble = "".join(lines[: marks[0][0]])
        if preamble.strip():
            raw.append((None, 0, preamble))

    for position, (start, level, title) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        raw.append((title, level, "".join(lines[start:end])))

    return _finish(raw)


def _finish(raw: List[Tuple[Optional[str], int, str]]) -> Tuple[Section, ...]:
    """Assign identities, disambiguating repeats by occurrence."""
    seen: dict = {}
    sections: List[Section] = []
    for position, (title, level, body) in enumerate(raw, start=1):
        heading = _bounded(title) if title is not None else None
        base = slugify(heading) if heading else ("preamble" if title is None else "")
        if not base:
            base = "section-" + str(position)
        ordinal = seen.get(base, 0) + 1
        seen[base] = ordinal
        section_id = base if ordinal == 1 else base + "-" + str(ordinal)
        sections.append(
            Section(
                heading=heading,
                level=level,
                section_id=section_id,
                ordinal=ordinal,
                body=body,
            )
        )
    return tuple(sections)


def reference_slug(value: object) -> Optional[str]:
    """Turn a stored reference into a section identity, or ``None``.

    Stored references are opaque bounded strings that a person typed
    (`plan_checkpoint`, `pending_decision_ref`). The rule is deliberately
    literal: take whatever follows the last ``#`` if there is one, slug it the
    same way a heading is slugged, and match on equality. No fuzzy matching, no
    prefix matching, no scoring — a reference either names a section or it does
    not, and pretending otherwise would be the guessing this package refuses to
    do.
    """
    if not isinstance(value, str):
        return None
    text = value.rsplit("#", 1)[-1] if "#" in value else value
    slug = slugify(text)
    return slug or None


def find_section(sections: Tuple[Section, ...], section_id: str) -> Optional[Section]:
    for section in sections:
        if section.section_id == section_id:
            return section
    return None


__all__ = [
    "MAX_HEADING_CHARS",
    "Section",
    "find_section",
    "reference_slug",
    "slugify",
    "split_sections",
]
