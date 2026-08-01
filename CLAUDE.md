# CLAUDE.md

Read [`AGENTS.md`](AGENTS.md) — it contains the binding rules for coding agents in this
repository (candidate-slot-only work, Guardian off-limits without explicit scope, update-record
preservation, test requirements, Trust Core invariants).

Quick facts:

- Product: personal always-on AI workstation on Ubuntu Desktop; phone/tablet PWA; Guardian +
  A/B runtime slots; typed actions; replaceable adapters. See [`DESIGN.md`](DESIGN.md).
- Current milestone and technical defaults: [`ROADMAP.md`](ROADMAP.md).
- What exists vs planned vs deferred: [`STATUS.md`](STATUS.md).
- Decision record (including the 2026-08-01 pivot and Trust Core reclassification):
  [`DECISIONS.md`](DECISIONS.md).
- Tests: `python -m unittest discover -s tests -t .` from the repo root.
