# Security

## Reporting a vulnerability

Please report suspected security issues privately rather than opening a public issue. (The exact
reporting channel is finalized before v0.1.0 is public; this file will be updated with the
specific contact method at that time.)

## Security promises (v0.1)

- No model, API key, or network I/O anywhere in v0.1's code path.
- Every change requires an explicit, hash-bound, single-use, expiring human approval.
- The executor only ever runs a fixed, executor-constructed `git apply` invocation — no user- or
  proposal-controlled argument ever reaches a subprocess.
- Every approved change is recorded in a hash-chained, tamper-evident audit log.
- Fail-closed: any mismatch, expiry, replay, or guard disagreement results in nothing happening.

## Approval-state layer (PR3b) — assumptions and limitations

PR3b adds the durable, expiring, **single-use approval ledger** under the repository's own
`.cofferdam/` workspace (`approvals.jsonl`, guarded by `approvals.lock`). It is **non-executing**:
it does not apply changes, run subprocesses, or invoke `git`. It does **not** yet contain the act
that *creates* an approval — the human-mediated mint is PR3c1, and execution is PR3c2. The following
assumptions define what this layer does and does not protect:

- **Ledger integrity is the authorization property.** A change is authorized only by a valid,
  active, unconsumed record in the protected ledger. `bound_hash` proves *what* a change is
  (binding), never that it is authorized; `approval_id` is a random event identifier, **not a bearer
  token** — reading or guessing it grants nothing.
- **The guarantees are host-level, not Cofferdam-level.** They hold only for an agent operating
  through Cofferdam's supported interfaces in a host that prevents the agent from arbitrary file I/O
  and terminal (pty) allocation. **`pty.openpty()` is in the Python standard library and defeats any
  `isatty()` check**, so the interactive TTY gate (which arrives with the PR3c1 mint) is an
  intent/anti-automation layer, **not proof of a human**.
- **Arbitrary in-process Python or direct edits to `.cofferdam/` are outside v0.1 guarantees.** Any
  code that can import Cofferdam's internal modules or write the ledger file directly can forge
  authorization state. Package-level privacy (underscores, "internal" conventions) is not a security
  boundary. The OS user account and filesystem permissions are part of the trust base.
- **Clock-rollback residual (accepted for v0.1):** A system clock rollback into a still-valid
  interval can extend an approval's practical lifetime beyond the configured TTL. This is a known
  v0.1 limitation. Only a rollback to *before* an approval's creation time is detected. The TTL is
  fixed and short (5 minutes), and PR3c2 must re-check validity immediately before consumption/
  execution.
- **Advisory locks protect only cooperating processes.** The single-use guarantee relies on an
  advisory file lock, which may be unenforced on some network/overlay filesystems (older NFS, some
  FUSE and container bind mounts); a best-effort warning is emitted where this is detected.
- **Windows permission enforcement is not claimed.** POSIX file-mode and owner checks (`0700`/`0600`,
  owner match) are enforced on POSIX and **skipped on Windows**, where `os.chmod` does not provide an
  ACL-equivalent boundary; symlink/reparse rejection and byte-range locking are still enforced, and
  local-account/NTFS-ACL protection is part of the trust base. Junction/reparse detection
  completeness on Windows is a documented residual.
- **A corrupt or torn ledger fails closed with no automatic repair.** Any malformed line — **or a
  non-empty ledger that does not end in a complete, newline-terminated record** (e.g. a write torn by
  a crash mid-append) — makes the whole ledger unusable; no approval is treated as valid. In
  particular a torn final *consumption* record cannot silently disappear and resurrect a consumed
  approval. Recovery is a deliberate manual procedure: with no Cofferdam process running, inspect
  `.cofferdam/approvals.jsonl` and remove the corrupt trailing content; Cofferdam never auto-truncates
  or auto-repairs an authority store.

- **The supported interface takes no caller-controlled authority inputs.** `find_valid_approval` and
  `consume_approval` accept only a binding and a `RepoView`; they select the clock, store, lock,
  state paths, permission policy, and TTL themselves. Dependency injection exists only on unexported,
  underscore-prefixed internal seams used by tests and by the future PR3c wiring — reaching those (or
  the internal `_ApprovalStore`) via arbitrary in-process Python is outside the supported-interface
  guarantee, as is any direct import of private module members.

## Out of scope

Cofferdam's trust boundary does **not** protect against:

- A malicious or compromised operating system.
- A compromised or malicious `git` binary.
- A malicious local insider with direct filesystem/OS access, arbitrary in-process Python, or the
  ability to edit `.cofferdam/` directly.
- Implicit local network paths outside Cofferdam's own code (e.g. a `git` credential helper's own
  network use, or DNS resolution performed by the runtime itself) — see
  [`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md).

## Dependency / supply-chain policy

v0.1 is standard-library-first and dependency-minimal by design (see [`DESIGN.md`](DESIGN.md)).
Any dependency that is added is audited before adoption and pinned. This keeps the trust core's
supply-chain surface small and reviewable.

## Incident response (post-release)

Once v0.1.0 is public, a suspected trust-boundary defect is handled as: (1) acknowledge and assess
severity privately; (2) prepare a patch and a fresh release; (3) yank/flag the affected release if
it is actively unsafe; (4) publish an advisory describing the defect, the affected versions, and
the fix, once a patch is available. Security fixes are never silently folded into an unrelated
release without a changelog/advisory entry.
