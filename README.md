# Cofferdam


A controlled workspace where AI agents propose, reviews weigh in, and humans decide what crosses
the boundary.

A cofferdam is not just a barrier — it holds back the water to create a dry, safe worksite where
real work gets done. Cofferdam does the same for AI-assisted development: inside the boundary,
agents can propose changes and plans, reviews can weigh in, and only approved actions cross into
your codebase or execution. The default state is **held back** — fail-closed, not fail-open.

The v0.1 release starts with the model-free trust core: a deterministic, zero-network approval
boundary for file changes. Later versions add the Review Room for multi-model critique of plans
and diffs, then guarded agent workflows. The boundary comes first because the workspace is only
useful if actions cannot cross it by accident.

The design loop: an agent (or any proposal source) submits a **proposal** → a **deterministic
guard** classifies it (allowed / needs-approval / blocked) → a **dry-run** renders exactly what
will change → an explicit **human approval** is hash-bound to that exact change → the change is
**applied atomically** → every step is recorded in a **hash-chained audit log**. Model review —
when it exists, from a later version — is advice; the guard is the authority, and its verdict is
never relaxed by what a model says.

## Status

**v0.1 is the model-free trust core: nothing leaves your machine.** No model, no API key, no
network I/O — the boundary itself, proven deterministic and fail-closed before anything is built
on top of it. The design direction from there is a controlled AI worksite: multi-model review of
plans and diffs through the **Review Room**, then guarded agent command workflows. These are
design directions, not shipped features. Only v0.1 is a committed scope.

## Requirements

See [`TESTING.md`](TESTING.md) for supported platforms and runtime version floors.

## Documentation

- [`DESIGN.md`](DESIGN.md) — architecture and design principles.
- [`THREAT-MODEL.md`](THREAT-MODEL.md) — adversaries, assets, and mitigating invariants.
- [`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md) — data-flow and risk posture.
- [`SECURITY.md`](SECURITY.md) — vulnerability reporting and incident response.
- [`TESTING.md`](TESTING.md) — test strategy and coverage targets.
- [`PROVENANCE.md`](PROVENANCE.md) — clean-room provenance statement.

## Credit

The multi-model review idea is inspired by the "council" concept from
[karpathy/llm-council](https://github.com/karpathy/llm-council). Concept only — no code, text, or
design is derived from that project, and no endorsement is implied.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

Created and maintained by Efe Aydınalp.
