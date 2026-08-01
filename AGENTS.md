# AGENTS.md — rules for coding agents in this repository

You are working on **Cofferdam**, a personal always-on AI workstation for Ubuntu Desktop with a
Guardian-supervised A/B self-update loop. Read [`DESIGN.md`](DESIGN.md) for architecture,
[`ROADMAP.md`](ROADMAP.md) for the current milestone, [`STATUS.md`](STATUS.md) for what exists,
and [`DECISIONS.md`](DECISIONS.md) before proposing direction changes.

## Hard rules (do not violate)

1. **Never modify the active runtime slot directly.** All implementation work happens in the
   inactive candidate slot/worktree (or, before the slot layout exists, in a feature branch
   worktree). If you cannot determine which slot is active, stop and ask.
2. **Never modify Guardian (`guardian/`) without an explicitly scoped task that names it.**
   Guardian changes follow the stricter high-risk path in D-2026-08-01-6. There is no
   automatic Guardian self-modification.
3. **Never bypass activation and rollback.** Do not switch traffic, edit Guardian state,
   restart the active service, or mark a candidate healthy yourself. Guardian and the user do
   that.
4. **Preserve update records.** When implementing a requested update, keep the original user
   prompt and acceptance criteria intact in the update record; never rewrite them to match what
   you built. `updates/` is append-only.
5. **Run the required tests** for the milestone/acceptance criteria before reporting a
   candidate ready; report failures honestly as failures. Deterministic tests are
   authoritative; your own assessment is advisory.
6. **Respect the disk separation** in `DESIGN.md`: never write secrets into git or code, never
   read `secrets/` into a prompt or log, never write outside the candidate worktree plus your
   designated state/log locations.
7. **Trust Core module** (`cofferdam/` guard/approval/executor code): its frozen invariants
   (fail-closed, deterministic guard, advisory-cannot-relax, I-16 no user-controlled subprocess
   argv, Linux/ext4 execution, Git never in the real-write path) remain binding whenever you
   touch it. Do not delete or rewrite Trust Core history. Incomplete executor work is preserved
   as a WIP commit on branch `pr3c2-candidate-b-execution` — do not rebase, rewrite, or merge
   that branch, and do not continue it unless a task explicitly scopes it.
8. **No monetization work** (subscriptions, hosted plans, pricing, enterprise/teams) — off the
   roadmap by D-2026-08-01-1.
9. **Preserve provenance**: keep the clean-room statements ([`PROVENANCE.md`](PROVENANCE.md),
   [`ATTESTATION.md`](ATTESTATION.md)) and the karpathy/llm-council concept credit intact; do
   not change `LICENSE`.

## Working style

- Typed actions over ad-hoc endpoints; adapters over direct tool calls; Cofferdam-owned schemas
  over any external tool's data model (OpenClaw's included).
- Wording about verification is evidential, never proof: "passed deterministic tests",
  "matched expected UI evidence" — not "proven correct".
- Review depth per D-2026-08-01-6: low-risk work needs tests + self-review only; do not
  manufacture review ceremony.
- Tests: `python -m unittest discover -s tests -t .` from the repo root (stdlib unittest;
  Trust Core tests are stdlib-only by policy).
