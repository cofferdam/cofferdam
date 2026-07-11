# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and this project does not yet have a stable
release.

## [Unreleased]

### Fixed

- **`FilesystemRepoView` now enforces its documented root containment** (PR02a). Previously the view
  joined caller components to the root and stat/opened the result directly, so a **direct** call with
  hostile parts (a `..` traversal, an absolute/drive/UNC/device component that replaces the root under
  `pathlib` join, or an intermediate symlink) could disclose out-of-root metadata via `path_type` or
  read out-of-root bytes via `read_bytes` — while the class docstring claimed escapes were reported
  fail-closed. (Supported proposal/guard/dry-run flows were **not** affected: `normalize_target`
  rejects such input before any view call and `canonicalize_target` re-checks real-path containment;
  no supported bypass existed.) The view now validates every component lexically before any filesystem
  access, fails closed on any intermediate symlink/reparse component, and checks that the resolved
  parent stays beneath the canonical root: an escape yields `PathType.MISSING` / `RepoReadError` with
  no outside path in the message, while a *final*-component symlink is still reported as `SYMLINK` and
  never followed. Containment is check-then-use (the local-account TOCTOU residual is documented; a
  descriptor-relative `openat`/`O_NOFOLLOW` traversal is deferred to PR4). The previously
  Windows-only-passing escape test is rewritten to be host-independent, and a full containment matrix
  is added. Also adds a minimal Ubuntu (`python 3.12`) GitHub Actions test workflow so the suite runs
  on POSIX in CI.

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
- Trust-core binding foundation (PR3a — **non-mutating**): domain-separated, length-prefixed SHA-256
  hashing utilities with frozen constants and known test vectors (`hashing.py`); read-only canonical
  target resolution that rejects symlink/reparse components, root escapes, and non-regular targets
  (`canonicalize.py`); a deterministic pre-state descriptor distinguishing absent / empty-regular /
  non-empty-regular (`prestate.py`); a bounded, symlink-rejecting read-only `read_bytes` on the repo
  view (`repo_view.py`); and an **immutable dry-run artifact** (`dryrun.py`). The artifact derives
  its patch bytes **internally** from the validated proposal (`proposal.diff.encode("utf-8")`) — one
  authoritative artifact, no independent caller-supplied patch — and takes its repository root
  **only** from the repo view (the view canonicalizes/validates its root and owns
  `root_real_path()`/`root_bytes()`), so path/root/pre-state cannot diverge across two roots. The
  binding hash uses `TAG_BOUND = "cofferdam.binding.v1"` and proves **binding, not authorization**.
  Standard library only; no file writes, no subprocess, no network. **No approval, nonce, ledger,
  expiry, TTY, `git apply`, executor, or audit exists yet** — those are PR3b/PR3c/PR3d.
- Approval-state layer (PR3b — **non-executing**): a durable, expiring, single-use approval ledger
  under the repository's own `.cofferdam/` workspace, with the strict approval/consumption record
  schemas and canonical JSONL serialization (`approval.py`), a deterministic fail-closed fold scoped
  by `repo_root_id`, the append-only store with a cross-process advisory lock, `fstat`-after-open
  permission/owner checks, symlink/reparse + non-directory rejection, fail-closed handling of
  torn/malformed records (**a non-empty ledger that does not end in a complete LF-terminated record
  invalidates the whole ledger — no automatic repair**, so a torn consumption line can never
  resurrect a consumed approval), consumption↔approval `bound_hash` cross-checking, size caps, and a
  write-all→fsync append protocol (`approval_store.py`), an injectable wall-clock abstraction
  (`clock.py`), and a **read-only** `cofferdam approval-status` command that creates no state.
  **Ledger integrity is the authorization property**: `approval_id` is a random event identifier,
  never a bearer token, and `bound_hash` alone never authorizes. The supported public functions
  `find_valid_approval(bound_hash, repo_view)` / `consume_approval(bound_hash, repo_view)` take **no**
  caller-injectable clock/store/lock/path/TTL (dependency injection exists only on unexported
  internal seams for tests and future PR3c wiring); the store itself is internal-only
  (`_ApprovalStore`, no public append API). Single-use is regression-tested across two real OS
  processes. PR3b writes only its own `.cofferdam/` state files. Standard library only; no repository
  mutation, no `git apply`, no subprocess, no network, no audit. **Deliberately absent (deferred):
  the human-mediated approval mint and TTY confirmer (PR3c1), the byte-exact executor (PR3c2), and
  the hash-chained audit log (PR3d)** — there is no production path that *creates* an approval in this
  release. `prestate.py` now raises an explicit error (instead of `assert`) so its content-hash
  invariant survives `python -O`.
