# Testing

## Strategy

Cofferdam's trust boundary is proven by tests, not by dogfooding alone — a passing manual dogfood
run is a **complement** to the test suite, never a substitute for it.

## Framework

The test runner is the Python standard-library `unittest` module — no third-party test
dependency, consistent with the v0.1 dependency policy ([`DESIGN.md`](DESIGN.md)). Tests are
discovered from `tests/` and run with `python -m unittest discover -s tests -t .`. This choice is
fixed as of PR01.

## Coverage targets — trust-boundary code

The following are held to the highest coverage bar in the codebase, and every negative-first case
enumerated in [`THREAT-MODEL.md`](THREAT-MODEL.md) / the relevant PR file must have a
corresponding test:

- The deterministic guard (classification logic).
- Approval verification (hash binding, expiry, single-use, replay rejection).
- Path canonicalization and containment checks.
- The hash-chained audit log (append, verify, tamper detection).
- The `git apply` executor invocation construction (fixed-argv guarantee — invariant I-16).

## Property-based and fuzz testing (committed, not aspirational)

- **Property-based tests for the guard**: the guard must be proven deterministic (same input →
  byte-identical verdict, repeated) and proven to treat proposal content as inert data (no
  embedded instruction-like text changes the verdict).
- **Fuzz tests for patch parsing**: random and adversarially-malformed patch input must never
  produce an "allowed" verdict or a partial write; malformed input fails closed.

## Supported-environment statement (test matrix)

- **Operating systems:** Linux, macOS, Windows, and WSL.
- **Git:** a minimum version floor sufficient for reliable `apply --check` and symlink semantics
  (exact floor confirmed in PR02/PR03, where those semantics are exercised).
- **Runtime:** Python 3.9 or newer. The runtime is Python and the floor is fixed at 3.9 as of PR01
  (declared in `pyproject.toml`); it may be raised in a later version if a specific standard-library
  guarantee requires it.

Every trust-boundary test in the matrix above runs on **both** a Windows and a POSIX platform
before v0.1.0 ships — a Windows-only or POSIX-only green run is not sufficient, since path and
symlink semantics differ between them.

## What is not yet covered

This file is a skeleton until PR02 (guard) and PR03 (approval/executor/audit) land — at which
point the coverage-target list above is backed by an actual, linked test suite.
