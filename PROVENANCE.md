# Provenance

## Clean-room statement

Cofferdam was initialized as a **fresh clean-room repository**. Its foundation was written from
private planning documents and a black-box behavioral specification — describing what the system
should do, never how any prior implementation did it.

No source code, tests, comments, prompts, CLI flags, output formats, configuration, file
structure, or other implementation artifacts were copied from, or inspected in, any of:

- Any prior prototype or fork of this project.
- [`karpathy/llm-council`](https://github.com/karpathy/llm-council) (the upstream project the
  *concept* — not the code — is credited to; see below).
- Atticus (referenced only as a product/governance comparison during private planning; its source
  was never opened).

All public prose in this repository is newly written for Cofferdam.

## Concept credit

The multi-model review idea is inspired by the "council" concept from
[karpathy/llm-council](https://github.com/karpathy/llm-council). **Concept only** — no code, text,
or design is derived from that project, and no endorsement by that project is implied.

## Clean-room rules

- Fresh repository, no fork relationship to any prior prototype or to `karpathy/llm-council`.
- Apache-2.0 from the first commit.
- Concept attribution only — never a code, text, or design derivation, and no endorsement implied.
- No copied source code, tests, prompts, prose, CLI output formats, configuration, or
  implementation structure from any prior prototype, `karpathy/llm-council`, or Atticus.

## Verification (2026-08-01)

The clean-room claim above was re-checked mechanically, not merely asserted. Every source and
documentation file in this repository (83 files) was content-hashed and compared against the
retired prototype's full tree (3381 files). Exactly one file matched: an **empty**
`tests/__init__.py` (0 bytes), which carries no expressive content.

The same pass confirmed that this repository vendors no third-party source code, bundles no
minified or generated third-party assets, and that its web assets reference no external hosts.
All dependencies are ordinary package requirements resolved at install time, not copied code.

## Attestation

The maintainer's clean-room attestation — the dated statement and sign-off — is recorded in
[`ATTESTATION.md`](ATTESTATION.md).

## Full provenance record

The complete provenance record (specification history, design-decision trail) is maintained
privately and is not part of this public repository. This file states the public-facing
commitment; it does not reproduce private planning material.
