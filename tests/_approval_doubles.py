"""Deterministic, test-only doubles for the approval layer.

Underscore-prefixed (not collected as a test) and **never imported by production
code** (asserted by ``tests/test_pr3b_no_side_effects.py``). Provides a fake
clock and small builders so tests can seed ledger state through the real,
package-internal ``ApprovalStore.append_approval`` — there is deliberately no
production mint to reuse, and the test path must not become one.
"""

from __future__ import annotations

from typing import Optional


class FakeClock:
    """A deterministic wall clock. Not thread-safe by design (tests set it)."""

    def __init__(self, now: int) -> None:
        self._now = int(now)

    def now_epoch(self) -> int:
        return self._now

    def set(self, now: int) -> None:
        self._now = int(now)


def make_approval_entry(
    *,
    bound_hash: str,
    repo_root_id: str,
    approval_id: Optional[str] = None,
    created_at: int = 1000,
    ttl: int = 300,
    relative_path: str = "src/app.py",
    guard_risk: str = "low",
) -> dict:
    """Build a schema-valid ``approval`` entry dict for seeding."""
    return {
        "entry_type": "approval",
        "schema_version": 1,
        "approval_id": approval_id or ("a" * 64),
        "bound_hash": bound_hash,
        "repo_root_id": repo_root_id,
        "relative_path": relative_path,
        "guard_risk": guard_risk,
        "created_at": created_at,
        "expires_at": created_at + ttl,
    }


def seed_approval(store, entry: dict) -> None:
    """Seed one approval through the real store under its lock (create=True).

    Uses the internal ``_append_approval`` write seam — this is a test-only
    helper, not a supported mint; production ships no approval-creation path."""
    with store.lock(create=True):
        store._append_approval(entry)
