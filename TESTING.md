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

## What is covered so far

- **PR2a:** the strict proposal parser, path normalization/containment, the protected-path matcher,
  the read-only repo view, and the no-`ALLOWED` verdict vocabulary — all with negative-first tests
  (schema rejection, path-traversal/containment, protected paths, symlink/non-regular targets,
  determinism, and a no-network/no-subprocess/fail-closed suite).
- **PR2b:** the deterministic guard and the positive-grammar diff validator — negative-first tests for
  malformed / multi-file / binary / truncated / oversized diffs, strict diff-path matching (including
  the one-sided `---`/`+++` mismatch bypass), Tier-1 and Tier-2 protected paths, symlink and
  non-regular targets, injection-as-data, the frozen `evaluate` signature, byte-stable verdict
  serialization over repeated evaluations, the fail-closed `try/except` wrapper (asserted directly,
  not inferred from fuzzing), the parse-before-evaluate contract, and a no-side-effects suite that
  sabotages `socket`, `subprocess`, and write-mode `open`.
- **PR3a (binding foundation, non-mutating):** frozen known-vector tests for every domain-separated
  hash and the `bound_hash` (recomputed independently from hardcoded tag literals + an 8-byte
  big-endian length prefix, so a serialization change breaks them), plus length-prefix
  boundary-ambiguity and field-reordering/sensitivity tests; pre-state sentinel vectors (absent vs
  empty-regular vs regular) and their distinctness/determinism; bounded `read_bytes` (absent vs
  empty, at/over the 10 MiB limit, directory/symlink rejected, no writes); canonicalization against
  real temp dirs (regular/absent, symlink component/target/escape rejected, non-regular rejected,
  determinism); the immutable content-light dry-run artifact (deterministic, no approval/nonce
  fields, blocked/oversize fail closed, injection-as-data inert, building mutates nothing); and a
  PR3a no-side-effects suite that sabotages `socket`, `subprocess`, write-mode `open`,
  `Path.write_text`/`write_bytes`, `os.remove`/`unlink`/`replace`/`rename`/`mkdir`/`makedirs`/`chmod`.
  Symlink-dependent cases skip cleanly where the platform cannot create a symlink. Balanced-review
  regressions: the dry-run binds exactly `proposal.diff.encode("utf-8")` (no independent
  caller-supplied patch — signature has only `proposal, repo_view`; a diff change moves both
  `patch_hash` and `bound_hash`), and the repository root is owned by the view (no separate
  `repo_root` argument; two roots give distinct `repo_root_id`/`bound_hash`; a non-existent root is
  rejected; production code is proven not to import the test double).

- **PR3b (approval-state layer, non-executing):** strict record validation (unknown/missing keys,
  bool-as-int, hash/`approval_id` format, risk enum, `created_at`/`expires_at` relationship, path
  traversal/control chars, oversize) and canonical-JSON golden vectors; the deterministic fold
  (active/expired/consumed, `repo_root_id` filtering of foreign-clone records, duplicate `approval_id`
  and ambiguous-active fail-closed, unknown/duplicate consumption, fresh-after-expiry); time
  boundaries (`now` before `created_at` → void, `== created_at` active, `== expires_at` expired,
  rollback); the store's read/parse — **a non-empty ledger that does not end in a complete
  LF-terminated record fails closed** (a torn final *approval* or *consumption* line, malformed
  middle line, bad UTF-8, oversize line, and entry-count cap all invalidate the whole ledger, with
  **no auto-repair**), plus the write-all/fsync protocol; the **single-use regression** that a torn
  final consumption line can no longer permit a second consume; data integrity (a consumption whose
  `bound_hash` does not match its referenced approval, or that references an unknown approval,
  invalidates the ledger); repository scope (a foreign-`repo_root_id` approval + its matching
  consumption cannot cross-authorize a local binding); path safety (non-directory `.cofferdam/`,
  symlinked ledger, broad POSIX permissions all fail closed); **cross-process** single-use via real
  `multiprocessing` (`spawn`) — two OS processes race to consume one approval and exactly one
  succeeds (exercising `flock` on POSIX and `msvcrt.locking` on Windows) — plus a bounded-timeout
  fail-closed lock test; write robustness (partial `os.write` completes via the write-all loop, a
  zero-byte write fails closed, a write/fsync error yields no success and never silently corrupts);
  the read-only `approval-status` CLI (exit 0/1/2, reads `--file`/stdin, **creates no state**, no
  patch/file content in output); and a PR3b authority/no-side-effects suite proving there is **no
  production mint symbol**, no `confirm`/`TtyConfirmer`, no forbidden import
  (`socket`/`subprocess`/`secrets`/…), no `token_hex`/`os.system` use, no production import of the
  test double, no `approve`/`execute` command, **the supported `find_valid_approval`/`consume_approval`
  wrappers take only `(bound_hash, repo_view)` — no injectable clock/store/lock/path/TTL** (DI lives
  only on unexported `_`-prefixed internal seams), the store class is internal-only (`_ApprovalStore`,
  no public `append_approval`), no patch/file content persisted, and that the full lookup/consume
  flow runs with `socket`/`subprocess` sabotaged and writes only under `.cofferdam/`. Symlink-
  dependent cases skip cleanly where the platform cannot create a symlink; POSIX-permission cases
  skip on Windows.

## What is not yet covered

The human-mediated approval *mint* (PR3c1), the `git apply` executor (PR3c2), and the audit chain
(PR3d) are not yet implemented; the corresponding coverage targets above (the `git apply` executor
and its fixed-argv guarantee, the audit chain) are backed by tests when those PRs land. PR3b
deliberately ships **no** approval mint, no nonce, no TTY confirmer, no subprocess, and no repository
mutation — only the durable single-use approval-state store and a read-only status command.
