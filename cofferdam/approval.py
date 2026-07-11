"""Approval-state model, strict record validation, fold, lookup, and consume.

PR3b is the **non-executing** authorization-state layer. It turns PR3a's binding
(``bound_hash`` = *what* a change is) into durable, expiring, single-use
authorization *state* on disk — but it deliberately does **not** contain the act
that *creates* an approval. There is **no mint here**: no ``create_approval``, no
nonce, no ``approval_id`` generation, no human confirmer. Minting is PR3c1.

**Ledger integrity is the authorization property.** An approval is authoritative
because a valid, active, unconsumed record for this ``bound_hash`` + ``repo_root_id``
exists in the protected ledger (see ``approval_store``). ``approval_id`` is a
random 256-bit event identifier (generated only by PR3c1), **never a bearer
token**: reading or guessing it grants nothing. Knowledge of ``bound_hash`` never
authorizes anything either.

This module owns: the frozen record schemas + strict validation, the canonical
JSON-line serialization, the deterministic fail-closed fold, and the two
PR3c-facing primitives ``find_valid_approval`` (lookup) and ``consume_approval``
(atomic single-use consume). All I/O and locking live in ``approval_store``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional, Sequence

from . import hashing
from .clock import Clock, SystemClock

# --- frozen schema constants -------------------------------------------------

SCHEMA_VERSION = 1
ENTRY_APPROVAL = "approval"
ENTRY_CONSUMPTION = "consumption"

# TTL is the PR3c1 *minting* contract (5 minutes). PR3b does not mint; it only
# validates the created_at/expires_at relationship on stored records.
TTL_SECONDS = 300

# Field/record caps (also enforced by approval_store on read/append).
MAX_RELATIVE_PATH_CHARS = 4096

STATE_ACTIVE = "active"
STATE_EXPIRED = "expired"
STATE_CONSUMED = "consumed"

_APPROVAL_KEYS = frozenset(
    {
        "entry_type",
        "schema_version",
        "approval_id",
        "bound_hash",
        "repo_root_id",
        "relative_path",
        "guard_risk",
        "created_at",
        "expires_at",
    }
)
_CONSUMPTION_KEYS = frozenset(
    {"entry_type", "schema_version", "approval_id", "bound_hash", "consumed_at"}
)

# guard_risk is display-only and excluded from authority decisions. The guard
# only ever emits "low" or "high" for a needs-approval verdict.
_RISK_VALUES = frozenset({"low", "high"})

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


class ApprovalError(Exception):
    """Fail-closed: an approval cannot be looked up, validated, or consumed."""


class LedgerError(ApprovalError):
    """The ledger (or an entry) is malformed, tampered, or ambiguous. The whole
    ledger is treated as invalid — no automatic repair, no skip-and-continue."""


@dataclass(frozen=True)
class ApprovalView:
    """A non-sensitive projection of an approval record. Carries no secret; the
    ``approval_id`` is an identifier, not a bearer token."""

    approval_id: str
    bound_hash: str
    repo_root_id: str
    relative_path: str
    guard_risk: str
    created_at: int
    expires_at: int
    state: str


# --- strict record validation (fail-closed) ---------------------------------


def _reject_constant(value: str) -> float:  # pragma: no cover - trivial
    raise LedgerError("non-finite JSON literal is not permitted")


def _require_int(obj: Mapping, key: str) -> int:
    value = obj[key]
    # bool is an int subclass; reject it where an integer is required.
    if not isinstance(value, int) or isinstance(value, bool):
        raise LedgerError(f"field {key!r} must be an integer")
    return value


def _require_hex64(obj: Mapping, key: str) -> str:
    value = obj[key]
    if not isinstance(value, str) or not _HEX64.match(value):
        raise LedgerError(f"field {key!r} must be 64 lowercase hex chars")
    return value


def _require_relative_path(obj: Mapping) -> str:
    value = obj["relative_path"]
    if not isinstance(value, str):
        raise LedgerError("relative_path must be a string")
    if value == "" or len(value) > MAX_RELATIVE_PATH_CHARS:
        raise LedgerError("relative_path length out of range")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise LedgerError("relative_path contains control characters")
    if value.startswith("/") or "\\" in value:
        raise LedgerError("relative_path is not a repo-relative posix path")
    if len(value) >= 2 and value[1] == ":":
        raise LedgerError("relative_path contains a drive letter")
    for part in value.split("/"):
        if part in ("", ".", ".."):
            raise LedgerError("relative_path contains an unsafe component")
    return value


def validate_approval_entry(obj: object) -> dict:
    """Strictly validate a raw ``approval`` mapping, fail-closed. Returns the
    canonicalized dict on success; raises ``LedgerError`` otherwise."""
    if not isinstance(obj, Mapping):
        raise LedgerError("entry is not a JSON object")
    keys = set(obj.keys())
    if keys != _APPROVAL_KEYS:
        raise LedgerError("approval entry has unexpected or missing keys")
    if obj["entry_type"] != ENTRY_APPROVAL:
        raise LedgerError("entry_type mismatch")
    if _require_int(obj, "schema_version") != SCHEMA_VERSION:
        raise LedgerError("unsupported schema_version")
    approval_id = _require_hex64(obj, "approval_id")
    bound_hash = _require_hex64(obj, "bound_hash")
    repo_root_id = _require_hex64(obj, "repo_root_id")
    relative_path = _require_relative_path(obj)
    guard_risk = obj["guard_risk"]
    if guard_risk not in _RISK_VALUES:
        raise LedgerError("guard_risk is not an allowed value")
    created_at = _require_int(obj, "created_at")
    expires_at = _require_int(obj, "expires_at")
    if created_at < 0 or expires_at <= created_at:
        raise LedgerError("created_at/expires_at relationship is invalid")
    return {
        "entry_type": ENTRY_APPROVAL,
        "schema_version": SCHEMA_VERSION,
        "approval_id": approval_id,
        "bound_hash": bound_hash,
        "repo_root_id": repo_root_id,
        "relative_path": relative_path,
        "guard_risk": guard_risk,
        "created_at": created_at,
        "expires_at": expires_at,
    }


def validate_consumption_entry(obj: object) -> dict:
    """Strictly validate a raw ``consumption`` mapping, fail-closed."""
    if not isinstance(obj, Mapping):
        raise LedgerError("entry is not a JSON object")
    keys = set(obj.keys())
    if keys != _CONSUMPTION_KEYS:
        raise LedgerError("consumption entry has unexpected or missing keys")
    if obj["entry_type"] != ENTRY_CONSUMPTION:
        raise LedgerError("entry_type mismatch")
    if _require_int(obj, "schema_version") != SCHEMA_VERSION:
        raise LedgerError("unsupported schema_version")
    approval_id = _require_hex64(obj, "approval_id")
    bound_hash = _require_hex64(obj, "bound_hash")
    consumed_at = _require_int(obj, "consumed_at")
    if consumed_at < 0:
        raise LedgerError("consumed_at is invalid")
    return {
        "entry_type": ENTRY_CONSUMPTION,
        "schema_version": SCHEMA_VERSION,
        "approval_id": approval_id,
        "bound_hash": bound_hash,
        "consumed_at": consumed_at,
    }


def parse_entry_line(line: str) -> dict:
    """Parse and strictly validate one canonical JSON line into a typed entry.

    Raises ``LedgerError`` (fail-closed) on any malformed content."""
    try:
        obj = json.loads(line, parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise LedgerError("entry is not valid JSON") from exc
    if not isinstance(obj, Mapping):
        raise LedgerError("entry is not a JSON object")
    entry_type = obj.get("entry_type")
    if entry_type == ENTRY_APPROVAL:
        return validate_approval_entry(obj)
    if entry_type == ENTRY_CONSUMPTION:
        return validate_consumption_entry(obj)
    raise LedgerError("unknown entry_type")


def canonical_line(entry: Mapping) -> str:
    """Canonical, byte-stable serialization of an entry (no trailing newline):
    sorted keys, fixed separators, ASCII-only."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# --- deterministic fold ------------------------------------------------------


def fold_active(
    entries: Sequence[Mapping], current_root_id: str, now: int
) -> Dict[str, ApprovalView]:
    """Fold the (already strictly-parsed) ledger entries into the set of
    currently-**active** approvals for ``current_root_id``, keyed by ``bound_hash``.

    Fail-closed (``LedgerError``) on: a duplicate ``approval_id`` among approval
    entries, a consumption referencing an unknown ``approval_id``, or two active
    approvals sharing a ``bound_hash`` (ambiguity). Approvals for a different
    ``repo_root_id`` are filtered out (a ledger copied from another clone cannot
    grant authority here). Duplicate consumptions are idempotent.
    """
    approvals: List[Mapping] = []
    approval_bound: Dict[str, str] = {}
    consumed: set = set()
    consumptions: List[Mapping] = []

    for entry in entries:
        etype = entry["entry_type"]
        if etype == ENTRY_APPROVAL:
            aid = entry["approval_id"]
            if aid in approval_bound:
                raise LedgerError("duplicate approval_id")
            approval_bound[aid] = entry["bound_hash"]
            approvals.append(entry)
        elif etype == ENTRY_CONSUMPTION:
            consumptions.append(entry)
        else:  # pragma: no cover - parse_entry_line already rejects this
            raise LedgerError("unknown entry_type in fold")

    for consumption in consumptions:
        ref = consumption["approval_id"]
        if ref not in approval_bound:
            raise LedgerError("consumption references an unknown approval")
        if consumption["bound_hash"] != approval_bound[ref]:
            # A consumption must name the exact binding of the approval it
            # consumes; a mismatch is a tampered/inconsistent ledger.
            raise LedgerError("consumption bound_hash does not match its approval")
        consumed.add(ref)

    active: Dict[str, ApprovalView] = {}
    for entry in approvals:
        if entry["repo_root_id"] != current_root_id:
            continue  # foreign clone: never active here
        aid = entry["approval_id"]
        created_at = entry["created_at"]
        expires_at = entry["expires_at"]
        if aid in consumed:
            continue  # consumed: terminal, not active
        if now < created_at:
            continue  # clock rollback before creation: void, not active
        if now >= expires_at:
            continue  # expired: terminal, not active
        bound = entry["bound_hash"]
        if bound in active:
            raise LedgerError("ambiguous: multiple active approvals for one binding")
        active[bound] = ApprovalView(
            approval_id=aid,
            bound_hash=bound,
            repo_root_id=entry["repo_root_id"],
            relative_path=entry["relative_path"],
            guard_risk=entry["guard_risk"],
            created_at=created_at,
            expires_at=expires_at,
            state=STATE_ACTIVE,
        )
    return active


# --- PR3c-facing primitives --------------------------------------------------


def _current_root_id(repo_view) -> str:
    return hashing.repo_root_id(repo_view.root_bytes())


# --- supported public surface (NO caller-controlled authority dependencies) --


def find_valid_approval(bound_hash: str, repo_view) -> Optional[ApprovalView]:
    """Supported, read-only lookup: the single active approval for ``bound_hash``
    in this repository, or ``None``. Selects its production dependencies
    **internally** — a ``SystemClock`` and the fixed store rooted only at
    ``repo_view`` — so callers cannot inject a clock, store, lock, state path,
    TTL, or timestamps. Non-authority; creates no state."""
    from .approval_store import _ApprovalStore

    return _find_valid_approval(
        bound_hash, store=_ApprovalStore(repo_view), repo_view=repo_view, clock=SystemClock()
    )


def consume_approval(bound_hash: str, repo_view) -> ApprovalView:
    """Supported single-use consume: atomically transition the single active
    approval for ``bound_hash`` to consumed, returning the consumed view; raises
    ``ApprovalError`` (fail-closed) otherwise. Selects production dependencies
    internally (no caller-controlled clock/store/lock/path/TTL). PR3b never
    executes; this is the primitive PR3c2 drives before an apply."""
    from .approval_store import _ApprovalStore

    return _consume_approval(
        bound_hash, store=_ApprovalStore(repo_view), repo_view=repo_view, clock=SystemClock()
    )


# --- unsupported internal seams (DI for tests and PR3c wiring only) ----------
# These accept injectable dependencies for deterministic tests and for PR3c's
# trusted internal wiring. They are NOT in ``__all__`` and are NOT a supported
# public API — arbitrary in-process import/use of private module members is
# outside the supported-interface guarantee (see SECURITY.md).


def _find_valid_approval(
    bound_hash: str, *, store, repo_view, clock: Clock
) -> Optional[ApprovalView]:
    if not store.state_exists():
        return None
    now = clock.now_epoch()
    root_id = _current_root_id(repo_view)
    with store.lock(create=False):
        entries = store.read_entries()
        active = fold_active(entries, root_id, now)
        return active.get(bound_hash)


def _consume_approval(bound_hash: str, *, store, repo_view, clock: Clock) -> ApprovalView:
    if not store.state_exists():
        raise ApprovalError("no active approval for this binding")
    now = clock.now_epoch()
    root_id = _current_root_id(repo_view)
    # The re-read, fold, re-check, append, and fsync all happen while holding the
    # same exclusive lock, so concurrent double-use yields exactly one success.
    with store.lock(create=False):
        entries = store.read_entries()
        active = fold_active(entries, root_id, now)
        view = active.get(bound_hash)
        if view is None:
            raise ApprovalError("no active approval for this binding")
        # Redundant explicit re-checks under the lock (defence in depth).
        if not (view.created_at <= now < view.expires_at):
            raise ApprovalError("approval is not active")
        entry = {
            "entry_type": ENTRY_CONSUMPTION,
            "schema_version": SCHEMA_VERSION,
            "approval_id": view.approval_id,
            "bound_hash": view.bound_hash,
            "consumed_at": now,
        }
        store._append_consumption(entry)
        return replace(view, state=STATE_CONSUMED)


__all__ = [
    "TTL_SECONDS",
    "SCHEMA_VERSION",
    "ApprovalError",
    "LedgerError",
    "ApprovalView",
    "find_valid_approval",
    "consume_approval",
]
