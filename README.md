# Cofferdam

A deterministic safety layer between AI agents and your codebase.

A cofferdam holds back the water so work happens safely inside. Cofferdam (the tool) holds back
AI-proposed changes until they pass review, get explicit human approval, and can be applied
atomically with a full audit trail. The default state is **held back** — fail-closed, not
fail-open.

The design loop: an agent (or any proposal source) submits a **proposal** → a **deterministic
guard** classifies it (allowed / needs-approval / blocked) → a **dry-run** renders exactly what
will change → an explicit **human approval** is hash-bound to that exact change → the change is
**applied atomically** → every step is recorded in a **hash-chained audit log**. Model review is
advice; the guard is the authority, and its verdict is never relaxed by what a model says.

## Status

**v0.1 is the model-free trust core: nothing leaves your machine.** No model, no API key, and no
network I/O exist anywhere in v0.1 — the guard, approval, and executor are pure, local, and
offline. Multi-model review (BYOK) is planned for a later version; it is not yet built, and not a
promise — only v0.1 is a committed scope.

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
