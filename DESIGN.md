# Design

This is the public design doc for Cofferdam. `DESIGN.md` (this file) and `THREAT-MODEL.md` are the
authoritative, public description of what Cofferdam is and why it is safe.

## What Cofferdam is

A deterministic safety layer between AI agents and your codebase. AI agents propose changes;
Cofferdam decides, with a human in the loop, whether and how those changes are ever allowed to
touch the filesystem — without depending on a model's judgment as the authority.

## The design loop

1. **Propose** — an agent or any proposal source submits a change (a single-file unified diff,
   in-scope, strictly schema-validated).
2. **Guard** — a pure, deterministic function classifies the proposal: allowed, needs-approval, or
   blocked. No model is consulted; the guard is reproducible and cannot be argued with.
3. **Dry-run** — the exact change that would apply is rendered, byte-identical to what execution
   will produce.
4. **Approve** — a human approval is bound (by hash) to the exact target path, diff, and
   pre-change state. It is single-use and expires.
5. **Apply** — the change is applied atomically, re-checking the guard immediately before
   execution. The executor never touches the index, commits, or hooks — the human commits.
6. **Audit** — every step is recorded in a hash-chained, append-only audit log.

Model review — when it exists, from a later version onward — is **advice**, not authority. The
guard's verdict is never relaxed by what a model says.

## v0.1 scope: the Trust Core

v0.1 proves this loop **entirely model-free, offline, and zero-network.** No API key, no model
call, and no network I/O exist anywhere in v0.1's code path. v0.1 is the **only committed scope**
of this project; anything beyond it is a roadmap under active re-evaluation after real user
feedback, not a promise.

## Dependency policy

v0.1 is **standard-library-first and dependency-minimal.** Any dependency that is added must be
audited and pinned; a large dependency tree contradicts a project whose entire value proposition is
a small, trustworthy, auditable trust boundary. Later versions (a BYOK provider adapter, from the
review-room line onward) will necessarily add a small number of well-audited dependencies for
provider I/O — never inside the trust-core path itself.

## What v0.1 is not

No auto-execution, no arbitrary shell, no network execution endpoint, no premium/model
auto-selection, no persona behavior. See [`THREAT-MODEL.md`](THREAT-MODEL.md) for the full
adversary/asset model and [`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md) for the data-flow posture.

## Provenance

Cofferdam is a clean-room implementation, built from a black-box behavioral specification — see
[`PROVENANCE.md`](PROVENANCE.md). The multi-model review concept is credited to
[karpathy/llm-council](https://github.com/karpathy/llm-council) (concept only — no code or design
derived from that project).
