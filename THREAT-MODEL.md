# Threat model

**Status: the trust-core classifier is complete (PR2), and the PR3a **binding foundation** is in
place — read-only canonical target resolution, bounded pre-state reading, domain-separated hashing,
and an immutable dry-run artifact. The artifact derives its patch bytes **only** from the validated
`proposal.diff` (one authoritative artifact — a caller cannot validate one patch and bind another)
and takes its repository root **only** from the repo view (which canonicalizes/validates its own
root — a caller cannot hash one root while reading from another). That artifact's `bound_hash`
(`TAG_BOUND = "cofferdam.binding.v1"`) proves **what** a change is (binding); it is **not**
authorization. No approval, nonce, ledger, expiry, TTY, `git apply`, executor, or audit exists yet —
the mutating/approval/audit path (PR3b/PR3c/PR3d) is still
forthcoming; sections that depend on it are marked _(forthcoming)_. PR3a is strictly non-mutating:
it performs no file write, subprocess, or network I/O; `read_bytes` is size-bounded (10 MiB) and
symlink-rejecting; canonicalization rejects symlink/reparse components, root escapes, and
non-regular targets, with case-insensitive / macOS-NFD / Windows-subst edges documented as PR4
hardening. The frozen hash constants (tags, 8-byte big-endian length prefix, pre-state sentinels)
are pinned by known test vectors and are the serialization contract PR3b/PR3d will build on.**

Cofferdam's job is to decide, deterministically and with a human in the loop, whether an
AI-proposed change is ever allowed to touch the filesystem. This document enumerates who might
attack that boundary, what they are after, and which invariant stops them.

## Adversaries

- **A malicious or careless proposal source** — an AI agent or upstream tool submitting a crafted,
  oversized, malformed, or injection-worded proposal (including prompt-injection text embedded in a
  diff or a filename) to gain a change it was not granted.
- **A compromised or buggy upstream tool** feeding proposals into Cofferdam.
- **A local concurrent process** racing the classify → (later) approve → apply window (TOCTOU). The
  guard narrows this by refusing symlink components and using a single repo-view snapshot per
  evaluation; full closure is PR3 _(forthcoming)_.
- **Operator error** — an approval granted in haste _(the approval path is forthcoming; the guard
  already refuses to auto-clear anything)_.

## Assets

- The working tree **outside** the whitelisted root, and non-target files inside it.
- Git internals: the index, commit history, and hooks.
- CI / supply-chain surfaces (`.github/workflows/`, `.git/hooks/`, install-time-executing manifests).
- Secrets and credentials that may exist in the repo or environment.
- Cofferdam's own control surface (the rules that judge a proposal).
- The integrity of the audit log _(forthcoming, PR3)_.
- The user's trust that "held back" is the true default state.

## Mitigating invariants

Enforced as of PR2b:

- **I-1 deterministic guard** — `evaluate(proposal, repo_view)` is a pure function of its explicit
  inputs (plus one repo-view snapshot): no clock, no randomness, no environment or locale reads, no
  network, no subprocess, no mutation. Reasons are deduplicated and sorted by a fixed key, and the
  `Verdict` serializes **byte-identically** (canonical JSON: sorted keys, sorted reasons, fixed
  separators, `ensure_ascii`) so the PR3 audit chain can hash it.
- **I-2 proposal-as-data** — proposal content is inert data. Injection-worded diffs and filenames
  classify identically to neutral content; nothing in a proposal changes control flow.
- **I-3 advisory-cannot-relax** — `evaluate`'s signature is **frozen at exactly two positional
  parameters** `(proposal, repo_view)`. No model/advisory channel exists anywhere in the guard path,
  so nothing can relax a verdict. A test asserts the signature. Any future advisory layer must be a
  separate wrapper with no access to guard internals.
- **Diff structural integrity** — the diff validator accepts only a **narrow positive grammar** (one
  `---`/`+++` header pair, hunk headers, and ` `/`+`/`-` body lines whose counts match the header,
  plus the no-newline marker). Everything outside it is refused, so `diff --git`/`index`/mode/rename
  headers, binary patches, multi-file diffs, truncated hunks, and exotic constructs all fail closed
  by default rather than by enumeration. Newlines are normalized to `\n` before parsing.
- **Strict diff-path matching** — **every** path reference in the diff (both `---` and `+++`, minus
  one `a/`/`b/` prefix, with `/dev/null` handled for create/delete) must equal `target_path` **after
  normalization**, by exact equality — never a suffix or loose match. This closes the bypass where a
  crafted diff points `---` at a protected file and `+++` at an innocent one.
- **No `ALLOWED` state** — a file-edit proposal is only ever `BLOCKED` or `NEEDS_APPROVAL`. There is
  no auto-clear decision to be tricked into.
- **I-7 path containment** — a target path must resolve inside the whitelisted root: absolute paths,
  `..`/`.` segments, drive letters, UNC paths, alternate-data-stream colons, control characters,
  over-long/over-deep paths, and reserved device names are all refused. Separators are normalized;
  components are NFC-normalized and compared casefolded so `.GIT`/`.Git` cannot bypass a rule.
- **I-9 protected paths** — a two-tier deny list: Tier 1 (VCS internals, CI/supply-chain vectors,
  install-executing manifests, Cofferdam's own config) is unconditionally blocked; Tier 2 (secrets,
  `.gitattributes`) is forced to the highest-scrutiny non-blocked state. Symlink/reparse-point
  components and non-regular targets are refused through a read-only injected repo view.
- **I-10 fail-closed** — malformed, oversized, or unknown input is rejected, never silently accepted.
  Unknown/extra keys are refused at **parse time**, before the guard is ever invoked. Fail-closed is
  **architectural, not emergent**: a `try/except` wrapper at the `evaluate()` (and parser, and
  validator) entry point converts any internal error into a `BLOCKED` verdict, and each wrapper has
  its own dedicated test rather than relying on fuzzing to discover it.
- **I-14 zero network** — no network I/O, and no subprocess, anywhere in the trust core (asserted by
  tests that sabotage `socket`, `subprocess`, and write-mode `open` and then run the guard over a
  hostile batch).

Enforced as of PR3b (the non-executing approval-state layer):

- **I-5 expiry + single-use (persistence half)** — an approval is authoritative only as a valid,
  active, unconsumed record in the protected append-only ledger (`.cofferdam/approvals.jsonl`),
  scoped to this clone by `repo_root_id`, with a fixed short TTL. Consumption is atomic under a
  cross-process lock (append one consumption entry + fsync); concurrent double-use yields exactly one
  success; a consumed or expired record is terminal. **Ledger integrity is the authorization
  property** — `bound_hash` is binding only, and `approval_id` is a random identifier, never a bearer
  token. A corrupt/torn ledger fails closed (no approval valid; no auto-repair). The residual: an
  actor who can write `.cofferdam/` directly, or run arbitrary in-process Python, or roll the wall
  clock back into a still-valid interval, is outside v0.1 guarantees — see [`SECURITY.md`](SECURITY.md).

Forthcoming: **I-4** (the hash-bound approval is *created* by a human-mediated, TTY-gated mint) —
PR3c1; **I-6/I-8 at execution** (guard re-check and canonical real-path re-bind immediately before
apply) and **I-15/I-16** (executor never touches index/commits/hooks; no user- or proposal-controlled
argument reaches any subprocess) — PR3c2; **I-13** (hash-chained audit) — PR3d.

## Design settled for later PRs (documentation, not yet code)

- **Audit chain (PR3):** a genesis record not anchored to anything mutable/replayable; tamper-evident
  storage that chains each entry (with the residual truncation risk documented); atomic
  approve + guard-recheck; log rotation/size.
- **Approval-fatigue (PR3):** a real mechanism (back-off on consecutive approvals, batch-size caps,
  escalating confirmation for oversized/high-risk), consistent with advisory-cannot-relax.
- **Ongoing maintenance surface:** the protected-path list is static, so ecosystem-new vectors
  (`.devcontainer/`, `mise.toml`, `.husky/`, Renovate config, …) are unprotected until added.

## Known strictness (accepted trade-offs)

- The diff grammar accepts only the **minimal unified-diff form**. A raw `git diff` — which prepends
  `diff --git` and `index` lines — is rejected; a proposal must submit the `---`/`+++`/`@@` core.
  This is deliberate: it is how binary, rename, copy, and mode-change vectors are refused *by
  construction* rather than by blocklist.
- A blank context line must carry its leading space. A bare empty line inside a hunk is outside the
  grammar and fails closed.
- `repo_view` is supplied by the caller. Its answers can never *relax* a verdict — the lexical and
  protected-path gates run regardless of what it reports — but a deliberately lying view is outside
  the threat model, since the caller already controls the integration.

## Zero-network caveat

v0.1 is **not** OS-sandboxed. Zero-network is a code-level guarantee — Cofferdam invokes no
connecting code path — not a kernel-enforced one. Implicit paths (the runtime itself, a `git`
credential helper, DNS resolution) are a documented limitation, not eliminated by a firewall.

## Out of scope

A malicious operating system, a compromised or malicious `git` binary, and a malicious local insider
with direct filesystem/OS access are out of scope — see [`SECURITY.md`](SECURITY.md) and
[`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md).
