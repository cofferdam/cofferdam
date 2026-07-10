# Threat model

**Status: the proposal-schema and path-containment foundation (PR2a) is implemented and enforced.
The guard decision engine and diff validator (PR2b) and the approval/executor/audit path (PR3) are
still forthcoming; sections that depend on them are marked _(forthcoming)_.**

Cofferdam's job is to decide, deterministically and with a human in the loop, whether an
AI-proposed change is ever allowed to touch the filesystem. This document enumerates who might
attack that boundary, what they are after, and which invariant stops them.

## Adversaries

- **A malicious or careless proposal source** — an AI agent or upstream tool submitting a crafted,
  oversized, malformed, or injection-worded proposal (including prompt-injection text embedded in a
  diff or a filename) to gain a change it was not granted.
- **A compromised or buggy upstream tool** feeding proposals into Cofferdam.
- **A local concurrent process** racing the classify → (later) approve → apply window (TOCTOU). PR2a
  narrows this by refusing symlink components and using a single repo-view snapshot per assessment;
  full closure is PR3 _(forthcoming)_.
- **Operator error** — an approval granted in haste _(the approval path is forthcoming; PR2a already
  refuses to auto-clear anything)_.

## Assets

- The working tree **outside** the whitelisted root, and non-target files inside it.
- Git internals: the index, commit history, and hooks.
- CI / supply-chain surfaces (`.github/workflows/`, `.git/hooks/`, install-time-executing manifests).
- Secrets and credentials that may exist in the repo or environment.
- Cofferdam's own control surface (the rules that judge a proposal).
- The integrity of the audit log _(forthcoming, PR3)_.
- The user's trust that "held back" is the true default state.

## Mitigating invariants

Enforced as of PR2a:

- **I-2 proposal-as-data** — proposal content is inert data. Injection-worded diffs and filenames
  classify identically to neutral content; nothing in a proposal changes control flow.
- **I-7 path containment** — a target path must resolve inside the whitelisted root: absolute paths,
  `..`/`.` segments, drive letters, UNC paths, alternate-data-stream colons, control characters,
  over-long/over-deep paths, and reserved device names are all refused. Separators are normalized;
  components are NFC-normalized and compared casefolded so `.GIT`/`.Git` cannot bypass a rule.
- **I-9 protected paths** — a two-tier deny list: Tier 1 (VCS internals, CI/supply-chain vectors,
  install-executing manifests, Cofferdam's own config) is unconditionally blocked; Tier 2 (secrets,
  `.gitattributes`) is forced to the highest-scrutiny non-blocked state. Symlink/reparse-point
  components and non-regular targets are refused through a read-only injected repo view.
- **I-10 fail-closed** — malformed or unknown input is rejected, never silently accepted; the parser
  and path assessment never raise to the caller (a wrapper converts any internal error to a
  rejection).
- **I-14 zero network** — no network I/O, and no subprocess, anywhere in the schema/containment code
  path (asserted by a test that sabotages `socket` and `subprocess`).
- **I-1 determinism (foundation)** — schema parsing and path assessment are pure functions of their
  explicit inputs (and, for type checks, one repo-view snapshot); identical inputs give identical
  results. The full byte-stable verdict serialization lands with the guard in PR2b.
- **I-3 advisory-cannot-relax (seam)** — there is no model/advisory input anywhere in this code path
  that could relax a decision. The frozen guard signature that guarantees this structurally lands in
  PR2b.

Forthcoming: **I-4/I-5/I-6** (hash-bound, single-use, re-checked approval), **I-8** (canonical
real-path bound into the approval hash), **I-13** (hash-chained audit), **I-15/I-16** (executor never
touches index/commits/hooks; no user- or proposal-controlled argument reaches any subprocess) — all
PR3.

## Design settled for later PRs (documentation, not yet code)

- **Audit chain (PR3):** a genesis record not anchored to anything mutable/replayable; tamper-evident
  storage that chains each entry (with the residual truncation risk documented); atomic
  approve + guard-recheck; log rotation/size.
- **Approval-fatigue (PR3):** a real mechanism (back-off on consecutive approvals, batch-size caps,
  escalating confirmation for oversized/high-risk), consistent with advisory-cannot-relax.
- **Ongoing maintenance surface:** the protected-path list is static, so ecosystem-new vectors
  (`.devcontainer/`, `mise.toml`, `.husky/`, Renovate config, …) are unprotected until added.

## Zero-network caveat

v0.1 is **not** OS-sandboxed. Zero-network is a code-level guarantee — Cofferdam invokes no
connecting code path — not a kernel-enforced one. Implicit paths (the runtime itself, a `git`
credential helper, DNS resolution) are a documented limitation, not eliminated by a firewall.

## Out of scope

A malicious operating system, a compromised or malicious `git` binary, and a malicious local insider
with direct filesystem/OS access are out of scope — see [`SECURITY.md`](SECURITY.md) and
[`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md).
