"""Deterministic text classification, and an honest account of what it cannot do.

PR3's production validation established the fact this module exists for: a
`source_ref` can be perfectly semantic while the **text under it** contains
`slots/a`, a vault root or an operational home, because canonical project
documents legitimately discuss the machine they describe. Clean metadata is not
clean content, and a projection that claimed otherwise would be claiming it about
the half nobody checked.

Two outcomes, chosen by consequence
-----------------------------------

**Recognised local paths are replaced.** A decision that says "deployment flips
between `slots/a` and `slots/b`" is *about* something a reader needs; dropping it
would lose the meaning to protect a detail. :data:`PLACEHOLDER` keeps the sentence
and removes the location.

**Credential-shaped material omits the whole part.** No rewrite, no partial, no
masking of the middle. Redaction is a guess about which bytes mattered, and a
wrong guess about a secret is permanent and unobservable. The part is dropped and
the omission is recorded, which is the fail-closed direction.

What this is not
----------------

**This is not a proof of absence.** Pattern matching cannot establish that
arbitrary prose contains no secret, and nothing in this repository should be
written as though it can. The sanitizer is the *last* of several layers, not the
boundary itself — the narrow source allowlist, the default exclusion of all four
Global Mind roles, the structured Working Context field allowlist, the semantic
reference grammar and the byte budget in :mod:`.policy` do the load-bearing work,
and each of those denies by construction rather than by recognition.

The known residual limitations are listed in :data:`RESIDUAL_LIMITATIONS`,
carried on every projection, and asserted as executable tests in
``tests/test_cloud_projection_adversarial.py`` so that they stay true statements
rather than becoming stale caveats.

No model, no network, no filesystem
-----------------------------------

Every decision here is a regular expression over a string plus a set of literal
values the caller supplied. There is no tokenizer, no classifier, no lookup and
no read. Two runs over the same text in the same environment give the same
answer, which is what makes a projection reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, NamedTuple, Optional, Tuple

from .policy import REDACTION_PATH

#: What a recognised local path becomes. Deliberately not empty and deliberately
#: not a plausible path: a reader must be able to tell that something was removed
#: and that what stood there was a location.
PLACEHOLDER = "[redacted-path]"


@dataclass(frozen=True)
class HostRedactionEnvironment:
    """The literal host values this projection must never emit.

    **Supplied, never discovered.** The projector performs no filesystem read and
    resolves no root: it is handed the values it must recognise, which keeps it
    from becoming a second path authority beside the project registry and the
    vault grant (D-2026-08-13-1). A caller assembles this from what it already
    knows — `Config`'s home, the project's registered root, the grant's vault
    root — and the projector only ever compares strings.

    Every field is optional and the empty environment is legal, because the
    generic patterns below still apply. It is not *safe*, and it is not the
    default: :class:`~.service.ContextProjector` requires this argument, so a
    caller that has the host values cannot silently fail to pass them.
    """

    cofferdam_home: Optional[str] = None
    project_roots: Tuple[str, ...] = ()
    vault_roots: Tuple[str, ...] = ()
    slot_roots: Tuple[str, ...] = ()
    home_directories: Tuple[str, ...] = ()

    @classmethod
    def none(cls) -> "HostRedactionEnvironment":
        """An explicit empty environment, for a caller that genuinely has none.

        Named rather than defaulted so that "this projector knows no host values"
        is something somebody wrote down.
        """
        return cls()

    @property
    def literals(self) -> Tuple[str, ...]:
        """Every value, longest first, deduplicated.

        Longest first matters: the operational home is a prefix of the slot roots
        on a normal host, and replacing the prefix first would leave `/slots/a`
        dangling after the placeholder.
        """
        values: List[str] = []
        candidates = (
            [self.cofferdam_home]
            + list(self.project_roots)
            + list(self.vault_roots)
            + list(self.slot_roots)
            + list(self.home_directories)
        )
        for value in candidates:
            if isinstance(value, str) and value.strip() and value not in values:
                values.append(value)
        return tuple(sorted(values, key=len, reverse=True))


class Sanitized(NamedTuple):
    """The result: the text to use, what was done to it, and whether to refuse."""

    text: str
    redactions: Tuple[str, ...]
    sensitive: bool


# -- credential shapes -------------------------------------------------------

#: Conservative and specific. Each entry is a *shape* with a recognisable prefix
#: or structure rather than a guess about entropy, because an entropy threshold
#: over Markdown flags commit hashes, base64 examples and UUIDs in decision
#: records — and a check that fires constantly is one somebody turns off.
_SECRET_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{20,}"),
    # A URL carrying userinfo. `https://user:password@host` is a credential in a
    # shape that survives copy-paste, and it is the one URL form that is not
    # merely an address.
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]{0,31}://[^\s/@:]+:[^\s/@]+@"),
    # A provider or adapter session identifier, when it is labelled as one.
    re.compile(r"(?i)\bsession[_\- ]?id\b\s*[=:]\s*[A-Za-z0-9._\-]{8,}"),
)

#: `NAME=value` where the name reads like a credential. The value is examined
#: separately, because canonical documentation explains these variables
#: constantly and a rule that could not tell `TOKEN=<your-token>` from a real one
#: would omit most of this repository's own decision record.
#:
#: The host-specific prefix is **optional** (M2J PR3.5.1). It was mandatory until
#: the PR3.5 post-deployment validation, which meant `COFFERDAM_ACTIONS_TOKEN=`
#: matched and a bare `TOKEN=` did not — so the form most likely to appear in a
#: pasted snippet was the one form the policy could not see. The prefix is the
#: part that varies between hosts; the keyword is the part that carries the
#: meaning, and requiring the variable half to be present inverted that.
#:
#: The bound on the prefix stays: an unbounded run before a required literal is
#: what made a long token-free line backtrack from every start position, and that
#: cost 84 seconds once already.
_ENV_ASSIGNMENT = re.compile(
    r"\b(?:[A-Z][A-Z0-9_]{0,63})?"
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIALS?|AUTH)"
    r"\s*[:=]\s*(\S+)"
)

#: Values that are documentation rather than credentials.
_PLACEHOLDER_VALUES = frozenset(
    {
        "changeme",
        "example",
        "none",
        "null",
        "placeholder",
        "redacted",
        "secret",
        "tbd",
        "token",
        "value",
    }
)

#: The shortest value this treats as a real credential. Below it, false positives
#: over ordinary prose outweigh the protection, and the omission of a whole part
#: is too blunt to spend on `KEY=1`.
_MIN_SECRET_VALUE_CHARS = 12

#: Formatting characters removed before the second secret scan, so a value broken
#: by inline code or emphasis is still seen as the value it is. Underscore and
#: hyphen are **not** here: they occur inside real tokens and inside the variable
#: names above, and removing them would change what is being matched.
_FORMATTING = str.maketrans("", "", "`*")


def _is_placeholder(value: str) -> bool:
    stripped = value.strip().strip(".,;:)]}\"'")
    if not stripped:
        return True
    if stripped[0] in "<${" or stripped.startswith("{{"):
        return True
    lowered = stripped.lower()
    if lowered in _PLACEHOLDER_VALUES or lowered.startswith("your"):
        return True
    if re.fullmatch(r"[x*.\-_]+", stripped):
        return True
    return len(stripped) < _MIN_SECRET_VALUE_CHARS


def _looks_sensitive(text: str) -> bool:
    for candidate in (text, text.translate(_FORMATTING)):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(candidate):
                return True
        for match in _ENV_ASSIGNMENT.finditer(candidate):
            if not _is_placeholder(match.group(1)):
                return True
    return False


# -- path shapes -------------------------------------------------------------

#: A run of separators, wherever one separator is meaningful. POSIX collapses
#: `//` to `/`, so `/home//someone/x` and `/home/someone/x` name the same file —
#: and a pattern that accepts one slash while the kernel accepts many is a
#: difference nobody has to be clever to find (M2J PR3.5.1).
#:
#: The trailing `(?!/)` is load-bearing, and a plain `/+` is a performance
#: defect rather than a style preference. A run of separators is followed by a
#: required literal — `/+tmp`, `/home`, `slots` — so a bare `/+` matching a long
#: run and then failing that literal backtracks through every shorter length, at
#: every start position: quadratic, and 50 000 slashes took over four seconds.
#: Requiring the run to be maximal prunes those branches, because any shorter
#: match leaves a slash next and fails the lookahead immediately. It is the
#: portable spelling of an atomic group — possessive quantifiers need 3.11 and
#: this package supports 3.9.
#:
#: This is the same failure PR3.5 already paid for once, so the regression tests
#: in `tests/test_cloud_projection_adversarial.py` assert the growth stays linear
#: rather than trusting the reading above.
_SEP = r"/+(?!/)"

#: The same run, where it *starts* a pattern rather than joining two components.
#: `(?<!/)` is the other half of the cost fix: without it every position inside a
#: run of slashes is a candidate start, and each one rescans the rest of the run
#: before failing the literal that follows — linear work at linear positions, so
#: 32 000 slashes cost 0.85 seconds per known root. A run has exactly one
#: beginning, and this says so. Internal separators need no such guard: they are
#: always preceded by a name character.
_SEP_LEADING = r"(?<!/)/+(?!/)"

#: Anchored on things that genuinely are filesystem roots. Deliberately **not** a
#: general "slash-separated word" rule: `/api/tasks` is a route this product
#: documents constantly, `state/tasks/tasks.sqlite3` is a relative name, and
#: treating either as a location would shred canonical text to no benefit.
_PATH_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"~" + _SEP + r"[A-Za-z0-9._\-/]*"),
    re.compile(r"/home" + _SEP + r"[A-Za-z0-9._\-]+(?:" + _SEP + r"[A-Za-z0-9._\-]+)*"),
    re.compile(r"/root(?:" + _SEP + r"[A-Za-z0-9._\-]+)+"),
    re.compile(r"\bslots" + _SEP + r"[ab](?:" + _SEP + r"[A-Za-z0-9._\-]+)*"),
    re.compile(r"\.obsidian(?:" + _SEP + r"[A-Za-z0-9._\-]+)*"),
)


@lru_cache(maxsize=64)
def _literal_patterns(literals: Tuple[str, ...]) -> Tuple[re.Pattern, ...]:
    """Compile each known host value into a separator-tolerant pattern.

    Until M2J PR3.5.1 this was `str.replace`, and a substring test cannot see a
    separator the operator did not type: a caller that said "never emit
    `/home/me/cofferdam`" still emitted `/home//me/cofferdam`. Every literal
    separator becomes a run, and nothing else about the value is reinterpreted —
    the components are escaped, so a dot in a root is still a dot.

    Cached on the literals tuple because a projection sanitizes one part after
    another against the same environment, and recompiling per part would put the
    cost of the fix in the wrong place.
    """
    patterns = []
    for literal in literals:
        components = [re.escape(part) for part in literal.split("/") if part]
        if not components:
            continue
        prefix = _SEP_LEADING if literal.startswith("/") else ""
        patterns.append(re.compile(prefix + _SEP.join(components)))
    return tuple(patterns)

#: A URL is an address, not a filesystem authority, so its path is masked out of
#: the way before the patterns above run and restored afterwards. Without this,
#: `https://example.com/home/someone/guide.html` would be mangled into nonsense
#: while leaking nothing, which is the worst of both outcomes.
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]{0,31}://[^\s<>\"'`\]\)]+")

#: Private-use codepoints, so a mask token cannot collide with document text.
_MASK_OPEN = ""
_MASK_CLOSE = ""


def _mask_urls(text: str) -> Tuple[str, List[str]]:
    found: List[str] = []

    def take(match: re.Match) -> str:
        found.append(match.group(0))
        return _MASK_OPEN + str(len(found) - 1) + _MASK_CLOSE

    return _URL.sub(take, text), found


def _restore_urls(text: str, found: List[str]) -> str:
    for index, url in enumerate(found):
        text = text.replace(_MASK_OPEN + str(index) + _MASK_CLOSE, url)
    return text


def sanitize(text: str, environment: HostRedactionEnvironment) -> Sanitized:
    """Classify and, where it is safe to, transform. Never silently.

    Order is load-bearing:

    1. **Credential scan first.** If the part is going to be dropped, nothing
       else about it matters, and scanning the original text means a redaction
       cannot accidentally destroy the evidence that a secret was there.
    2. **Known host literals next**, everywhere — *including* inside URLs. A
       literal operational root is host-identifying wherever it appears, and the
       URL exemption is about generic path *shapes*, not about a value the caller
       explicitly said must never be emitted.
    3. **Generic path shapes last**, with URLs masked out of range.
    """
    if not isinstance(text, str) or not text:
        return Sanitized("", (), False)

    if _looks_sensitive(text):
        return Sanitized("", (), True)

    redacted = False
    result = text
    for pattern in _literal_patterns(environment.literals):
        result, count = pattern.subn(PLACEHOLDER, result)
        if count:
            redacted = True

    masked, urls = _mask_urls(result)
    for pattern in _PATH_PATTERNS:
        masked, count = pattern.subn(PLACEHOLDER, masked)
        if count:
            redacted = True
    result = _restore_urls(masked, urls)

    return Sanitized(result, (REDACTION_PATH,) if redacted else (), False)


#: What this module does **not** catch, stated so that nobody has to infer it.
#: Each line corresponds to a passing test in the adversarial suite, so the list
#: is a description of current behaviour rather than a disclaimer.
RESIDUAL_LIMITATIONS: Tuple[str, ...] = (
    "Pattern matching cannot prove text contains no secret; this is one layer, not the boundary.",
    "A credential in an unrecognised shape, or a passphrase in prose, is not detected.",
    "A credential variable name is matched in upper case only, as environment variables are written.",
    "A secret split across a line break is not reassembled, because that would change the text.",
    "A relative path with no recognised root is not treated as a location.",
    "Windows-style paths are not recognised; this product's hosts are Linux.",
    "Prose that describes where something lives is content, and is not redacted.",
)


__all__ = [
    "PLACEHOLDER",
    "RESIDUAL_LIMITATIONS",
    "HostRedactionEnvironment",
    "Sanitized",
    "sanitize",
]
