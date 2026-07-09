# Threat model

**Status: skeleton — finalized in PR02, alongside the pure deterministic guard.**

This is a one-page skeleton of Cofferdam's threat model. The full model — the complete adversary
list, asset list, and the mapping from each threat to the specific invariant that mitigates it —
lands in PR02, once the guard exists to be modeled against.

## Adversaries (draft)

- A **malicious or careless AI agent / proposal source** — may submit a crafted, oversized,
  malformed, or adversarially-worded proposal (including prompt-injection-style text embedded in a
  diff) attempting to gain execution it wasn't granted.
- A **compromised or buggy upstream tool** feeding proposals into Cofferdam.
- A **local concurrent process** (e.g. a hostile or buggy script) racing the approval/execution
  window (TOCTOU).
- **Operator error** — an approval granted in haste, without reading the dry-run.

## Assets (draft)

- The working tree / filesystem outside the explicitly whitelisted, in-scope directory.
- Git internals: the index, commit history, hooks.
- Secrets and credentials that may exist in the repo or environment.
- The integrity of the audit log itself.
- The user's trust that "held back" is the true default state.

## Mitigating invariants (draft — full set finalized in PR02/PR03)

Cofferdam's trust boundary is enforced by a numbered set of invariants (I-1 through at least I-16),
covering: guard determinism, proposal-as-data, containment, protected paths, fail-closed behavior,
approval hash-binding (including the canonicalized path bound into the hash), expiry/single-use
approval, guard re-check at execution time, no secret egress, hash-chained tamper-evident audit,
zero network, no index/commit/hook interaction by the executor, and no user- or proposal-controlled
argument ever reaching any subprocess. The full enumerated list, with test coverage for each, is
published here once PR02 (guard) and PR03 (executor) land.

## Out of scope (draft)

A malicious OS, a compromised `git` binary, or a malicious insider with direct filesystem access
are out of scope — see [`SECURITY.md`](SECURITY.md) for the full out-of-scope list.
