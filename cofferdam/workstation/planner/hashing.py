"""The fingerprint a human authority record is bound to.

What this exists to make impossible
-----------------------------------

A person is shown a prepared worker prompt and approves it. Later — a minute or
a month — something intends to dispatch a prompt. The only honest way to let it
proceed is for it to be able to prove *this is the same prompt*, and the only way
to prove that is to have bound the approval to the prompt's exact bytes at the
moment the approval was given.

So an authority record does not merely point at a planner request. It carries a
digest of the exact model output being authorized, and a dispatcher recomputes
that digest from what it is holding. If the two differ, the approval does not
authorize it — not because a flag was cleared, but because the record commits to
something else.

Why the tag is our own
----------------------

The Trust Core has this primitive already in :mod:`cofferdam.hashing`, and Mind
declined to import it for a reason that applies here word for word: that module's
serialization is a **frozen contract** its ledger and audit chain depend on, and
binding a different milestone's durable values to it would make either module's
evolution the other's migration. D-2026-08-11-4 adopts the Trust Core's posture —
fail-closed, hash-bound — "without importing its machinery yet".

The discipline is copied and the tag is this domain's own:

    SHA256( tag || lp(f0) || lp(f1) || ... )     lp(x) = len(x) as 8 bytes BE || x

Length-prefixing is what stops the fields aliasing. Without it a request id of
``plan_ab`` with prompt ``c`` and one of ``plan_a`` with prompt ``bc`` would hash
the same bytes, and two genuinely different subjects would compare equal — which
would mean an approval of one authorizing the other, the exact failure this
module exists to prevent.

What is bound in
----------------

Four fields, and each one is here because leaving it out has a failure:

``planner_request_id``
    Without it, two planning turns that happened to produce byte-identical
    prompts would share a fingerprint, and an approval of one would authorize the
    other.
``result_schema_version``
    The result contract the persisted row speaks. A future contract could give
    the same string a different meaning; an approval must not survive that
    silently.
``action``
    An answer to a question and an approval of a prompt are different authority.
    Binding the action in means their fingerprints cannot collide even if the
    question text and the prompt text were identical.
``subject``
    The exact model-authored bytes being authorized — the worker prompt, or the
    user question. Hashed as they are stored: not normalized, not stripped, not
    canonicalized. A normalization step would mean a change Cofferdam considered
    cosmetic could ride in under an approval somebody gave for the text they
    actually read.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: Versioned in the tag, because a stored fingerprint is durable. Changing what
#: is hashed must produce a *different* tag rather than quietly making old
#: records compare unequal for a reason nobody can see.
TAG_AUTHORITY_SUBJECT = b"cofferdam.planner.authority.subject.v1"

LENGTH_PREFIX_WIDTH = 8

#: Lowercase hex SHA-256.
FINGERPRINT_CHARS = 64

_HEX = frozenset("0123456789abcdef")


def _length_prefixed(value: bytes) -> bytes:
    return len(value).to_bytes(LENGTH_PREFIX_WIDTH, "big") + value


def authority_subject_fingerprint(
    *,
    planner_request_id: str,
    result_schema_version: int,
    action: str,
    subject: str,
) -> str:
    """The digest a human authority record commits to.

    Keyword-only on purpose. Four strings in a row is exactly the call somebody
    eventually gets in the wrong order, and a fingerprint computed over shuffled
    fields is not an error anything notices — it is a value that simply never
    matches again.
    """
    if not isinstance(planner_request_id, str) or not planner_request_id:
        raise TypeError("planner_request_id must be a non-empty string")
    if not isinstance(action, str) or not action:
        raise TypeError("action must be a non-empty string")
    if not isinstance(subject, str):
        raise TypeError("subject must be a string")
    if isinstance(result_schema_version, bool) or not isinstance(
        result_schema_version, int
    ):
        raise TypeError("result_schema_version must be an integer")

    hasher = hashlib.sha256()
    hasher.update(TAG_AUTHORITY_SUBJECT)
    for field in (
        planner_request_id.encode("utf-8"),
        str(result_schema_version).encode("ascii"),
        action.encode("utf-8"),
        subject.encode("utf-8"),
    ):
        hasher.update(_length_prefixed(field))
    return hasher.hexdigest()


def valid_fingerprint(value: Any) -> bool:
    """Shape check for a fingerprint read back out of the database or a caller.

    Not the security boundary — the comparison is — but a stored value that is
    not a digest should fail closed where it is read rather than compare unequal
    and be reported as ordinary drift.
    """
    return (
        isinstance(value, str)
        and len(value) == FINGERPRINT_CHARS
        and all(character in _HEX for character in value)
    )


__all__ = [
    "FINGERPRINT_CHARS",
    "LENGTH_PREFIX_WIDTH",
    "TAG_AUTHORITY_SUBJECT",
    "authority_subject_fingerprint",
    "valid_fingerprint",
]
