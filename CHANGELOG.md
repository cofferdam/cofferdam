# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and this project does not yet have a stable
release.

## [Unreleased]

### Added

- Foundation docs (PR0): `LICENSE`, `README.md`, `PROVENANCE.md`, `SAFETY-AND-RISK.md`,
  `SECURITY.md`, `TESTING.md`, `DESIGN.md`, `THREAT-MODEL.md`, `AUTHORS.md`, `.gitignore`, and a
  license-scan CI check. No product code yet.
- CLI skeleton (PR1): the `cofferdam` package with `--version`, help output, an empty command
  dispatch registry, the exit-code convention, and the stdout/stderr split. Standard-library only;
  no guard, executor, approval, audit, provider, or network behaviour yet.
- Trust-core foundation (PR2a): a strict fail-closed proposal schema/parser (`proposal.py`), path
  normalization + containment + protected-path matching (`paths.py`, `protected_paths.py`), a
  read-only injected repo view for symlink/type checks (`repo_view.py`), and the shared verdict
  vocabulary with a no-`ALLOWED` decision set (`verdict.py`). Finalized the PR2a-relevant sections of
  `THREAT-MODEL.md`. Standard-library only; no network, no subprocess, no file mutation. The guard
  decision engine and diff validator remain PR2b; approval/executor/audit remain PR3.
- Deterministic guard and diff validator (PR2b): `guard.py` with the frozen
  `evaluate(proposal, repo_view)` signature and an architectural fail-closed wrapper; `diffcheck.py`,
  a positive-grammar validator for the narrow git unified-diff subset (newline normalization, hunk
  line-count checking, and strict `---`/`+++` path matching against `target_path`); and the immutable
  `Verdict` container with byte-stable canonical serialization. Malformed, multi-file, binary,
  truncated, oversized, and path-mismatched diffs all fail closed. Still `BLOCKED`/`NEEDS_APPROVAL`
  only — no `ALLOWED`. Standard-library only; no network, no subprocess, no file mutation.
  Approval/executor/audit remain PR3.
