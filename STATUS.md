# Status

Accurate as of **2026-08-01** (the personal-AI-workstation pivot, [`DECISIONS.md`](DECISIONS.md)
D-2026-08-01-1). Update this file when a category changes, not on every commit.

## Merged (on `main`)

Trust Core module, PR0 → PR3c1:

- Foundation docs, Apache-2.0 license, clean-room provenance/attestation, CI license scan (PR0).
- `cofferdam` CLI skeleton: `--version`, dispatch registry, exit-code convention (PR1).
- Fail-closed proposal schema/parser, path normalization + containment + protected paths,
  read-only repo view, verdict vocabulary (PR2a).
- Deterministic guard (`evaluate(proposal, repo_view)`), strict unified-diff validator,
  byte-stable verdict serialization (PR2b); RepoView root-containment fix + Ubuntu CI (PR2a fix).
- Binding foundation: domain-separated hashing, canonical target resolution, pre-state
  descriptor, immutable dry-run artifact (PR3a).
- Durable single-use expiring approval ledger (PR3b).
- Interactive human approval mint: `cofferdam approve` (PR3c1).

## Implemented but uncommitted

- **PR3c2 — Candidate-B byte-exact executor.** Lives only in the worktree
  `C:\cofferdam\worktrees\pr3c2-candidate-b-execution` (branch `pr3c2-candidate-b-execution`)
  as 18 modified/untracked files (`executor.py`, `execute_cli.py`, `execstate.py`,
  `postimage.py`, `platform_support.py`, tests). **Do not discard, clean, or rebase that
  worktree.** Recommendation R-1 in [`DECISIONS.md`](DECISIONS.md): commit it to its branch as
  WIP to get it under version control.

## Planned (active roadmap — see [`ROADMAP.md`](ROADMAP.md))

- Guardian/Supervisor, Runtime A/B slots, phone/tablet PWA, typed actions, Ubuntu desktop
  control, screenshots, display targeting, browser/media control (YouTube, Netflix profile),
  Claude Code task adapter, update records, A/B self-update demonstration, natural-language
  intent routing (Ollama), OpenClaw spike.

## Deferred (preserved, not on the critical path)

- Trust Core completion: landing PR3c2, PR3d hash-chained audit log, PR4 hardening. The module
  is preserved for future privileged-action and high-assurance-update use.
- Obsidian integration, vector/advanced memory, council/multi-model review integration, voice
  and wake words, native mobile apps, generalized multi-agent orchestration.
- Windows and macOS *host* support for the workstation product.
- Domain/trademark registration and any productization gates.

## Speculative (no commitment)

- Reusing the Trust Core approval boundary for Guardian updates, package installation,
  root-level operations, and destructive migrations.
- Additional client platforms beyond the PWA.

## Superseded

- The v0.2–v0.6 version-line roadmap (provider adapter / Review Room / allowlisted command
  workflow / parity / preferences-lenses / hosted-team-mobile) as a product plan.
- "v0.1 Trust Core is the only committed scope" and "nothing leaves your machine" as
  whole-product claims (they remain true of the Trust Core module).
- Per-PR council review gates as the default process.
- Monetization/productization planning (sponsors, design partners, hosted plans).
