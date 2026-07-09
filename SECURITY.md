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

## Out of scope

Cofferdam's trust boundary does **not** protect against:

- A malicious or compromised operating system.
- A compromised or malicious `git` binary.
- A malicious local insider with direct filesystem/OS access.
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
