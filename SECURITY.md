# Security

> **Scope note (2026-08-01):** the promises below describe the **Trust Core module** (see
> [`DECISIONS.md`](DECISIONS.md) D-2026-08-01-7). The workstation product being built per
> [`ROADMAP.md`](ROADMAP.md) is network-connected (private tailnet + device token) and will
> get its own security documentation as those components land. Vulnerability reporting below
> applies to the whole repository.

## Maturity statement — read this first

**Cofferdam is early-stage software and makes no claim of production-grade security.** It is a
personal tool under active development by a single maintainer. It has not been audited, has no
security release process yet, and its workstation surface has not been validated on its own
target platform (see [`STATUS.md`](STATUS.md)).

Deploy it only as designed: on a **private network (Tailscale), bound to a private interface,
behind a generated device token**, on a machine whose compromise you could tolerate. Do not
expose it to the internet, do not put it on a shared or untrusted network, and do not rely on it
to protect anything valuable.

## Workstation security posture (M1)

What the workstation service does today:

- Binds to `127.0.0.1` by default; a non-loopback bind is announced on stderr so an accidental
  public bind is visible. The intended production bind is the host's Tailscale address.
- Requires a `Bearer` device token on every route that reveals or changes host state; the token
  is compared with `secrets.compare_digest`, generated on first run, stored `0600` outside the
  repository, never returned by any endpoint, and never written to logs.
- Refuses to upgrade an unauthenticated WebSocket (closed before accept).
- Accepts **typed actions only** — no endpoint, schema field, or adapter accepts a command,
  argument vector, or shell string; `shell=True`/`os.system`/`os.popen` are absent and a test
  enforces their absence. Application names come from a closed allowlist; URLs must be http(s).
- Returns bounded structured errors rather than tracebacks.
- **Never takes over the desktop's own lifecycle.** The service observes and follows
  `graphical-session.target` through read-only queries; it never activates, starts, stops,
  restarts, or terminates it, and it never terminates a login session, the user manager, or
  GNOME. It cannot: no shipped unit, script, or source may name that target in an activating
  directive, and `systemctl --user exit`, `loginctl terminate-user`/`terminate-session`,
  `gnome-session-quit`, broad `pkill`/`killall`, and direct process signalling are all absent
  and structurally tested for. This is an **availability** property, and it was learned the hard
  way: the M1 unit's `Wants=graphical-session.target` left the host unable to complete a
  graphical login at all (`DECISIONS.md` D-2026-08-04-1,
  [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md)).
- **Never widens its own bind.** If the configured private address is unavailable, the service
  waits for it on a bounded timer and then exits — it does not fall back to a wildcard, and no
  code path can. Restarts are rate-limited, so a permanent failure cannot become a respawn storm.
- Unit files carry no secrets; the device token is read at runtime from a `0600` file.

What it deliberately does **not** do yet: TLS termination of its own (Tailscale provides the
transport boundary), token rotation or expiry, rate limiting, multi-user separation, audit
logging of actions beyond the bounded local action list, or any OS-level sandboxing of the
service from the desktop session it controls — by design, it *is* the desktop session's agent.

Anything privileged (package installation, system configuration, destructive migrations) is out
of scope for M1 and is the intended future home of the preserved Trust Core boundary.

## Runtime inventory posture (M2B)

M2B reads the machine. That puts a **new class of data on the wire** — what is plugged into
somebody's desk, and which applications they have open — and it is treated accordingly.

- **Authenticated, always.** `GET /api/runtime` and `GET /api/runtime/{resource_kind}` require the
  same device token as every other state-revealing route. There is no anonymous summary and no
  count endpoint, and the token is checked *before* any scan runs, so an unauthenticated request
  cannot even cause the host to walk `/proc`.
- **Read-only, structurally.** No route under `/api/runtime` accepts `POST`, `PUT`, `PATCH`, or
  `DELETE` — asserted by inspecting the registered routes rather than by trying a few verbs.
  Discovery starts, stops, moves, reconfigures, and terminates nothing. Process and window
  *control* is a later milestone with its own identity re-verification rules.
- **Command lines and environment blocks are never read.** `/proc/<pid>/cmdline` and
  `/proc/<pid>/environ` are not opened at all — not read-then-redacted. Both routinely carry an API
  key passed as an argument, a token in an environment variable, or a database URL with its
  password. Grouping is built on cgroup membership and process ancestry instead, and the absence is
  asserted structurally over the package source as well as behaviourally over the payload, so
  "just this once, for classification" cannot creep back in.
- **Window titles are not exposed**, because window enumeration is unavailable on this desktop at
  all. If a future backend provides them they are sensitive by default: never logged, never
  persisted, served only through the authenticated API. A window title is routinely a document
  name, a message subject, or a customer's name.
- **No browser profile inspection.** A running browser is discovered as one application instance
  from its processes. No profile directory, cookie store, history file, or tab list is read.
- **Identities are derived, not raw.** `/etc/machine-id`, `/proc/sys/kernel/random/boot_id`, and
  the graphical session's activation stamp are published as domain-separated SHA-256 prefixes.
  None is a secret, but all are stable global identifiers, and an authenticated client needs their
  comparison properties rather than the identifiers themselves.
- **Semantic interfaces only, at the discovery layer too.** Displays come from
  `org.gnome.Mutter.DisplayConfig` (read-only getters, named from a fixed table) and
  `/sys/class/drm`. No screenshot, no OCR, no pixel matching, and no `org.gnome.Shell.Eval` —
  evaluating JavaScript inside the compositor is arbitrary code execution in the user's shell, and
  is refused even though it is the one interface that could answer the window question. No GNOME
  extension is installed.
- **Failure is a status, not a crash.** Every backend degrades to `unavailable`/`error` with a
  bounded, code-owned reason carrying no path, command line, or exception text. A discovery fault
  cannot take down the daemon, the session, or the login.
- **Session-scoped data expires with the session.** A cached snapshot is discarded the moment the
  graphical session's identity changes, so a previous session's displays are never served as
  current.

Details, including every backend's limitations:
[`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md).

## Reporting a vulnerability

Please report suspected security issues **privately** rather than opening a public issue: email
the maintainer at the address on the GitHub profile for
[@EfeAydinalp](https://github.com/EfeAydinalp), or use GitHub's private vulnerability reporting
on this repository. Given the project's stage, expect best-effort, hobby-timescale responses —
there is no SLA.

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

## Approval mint (PR3c1) — assumptions and limitations

PR3c1 adds `cofferdam approve --file <proposal.json>`, the human-mediated command that *creates* an
approval record. It **executes nothing** — approval and execution are separate, and the byte-exact
executor is still PR3c2.

- **`cofferdam approve` is the only supported authority-mint path.** There is no public
  `create_approval` / `mint_approval` Python API and no other command that writes an approval. The
  internal mint takes no caller-controlled clock, store, lock, entropy source, TTL, `approval_id`,
  timestamp, state path, `bound_hash`, or `repo_root_id`; it selects the production `SystemClock`,
  `secrets.token_hex`, fixed 300-second TTL, and `_ApprovalStore` itself. As with the rest of the
  ledger layer, underscore-named internals are not a security boundary — an actor with arbitrary
  in-process Python or direct `.cofferdam/` write access is outside v0.1 guarantees.
- **No supported non-interactive approval exists.** There is no `--yes`, `--force`,
  `--non-interactive`, `--approve-anyway`, `--repo`, config file, or environment variable that mints
  or auto-approves, and the proposal is never read from stdin (stdin is reserved for the human's
  typed confirmation).
- **The TTY gate is intent/anti-automation, not proof of a human.** `approve` runs only when stdin,
  stdout, and stderr are all terminals. **`pty.openpty()` is in the Python standard library and
  satisfies `isatty()`**, so this gate filters accidental automation; it does not prove a human
  acted. The real authorization property remains ledger integrity. The typed confirmation line is
  bounded to **256 UTF-8 bytes** (measured in bytes, not characters); a longer line, an embedded
  control character, or any deviation from the exact phrase declines, one attempt only.
- **You approve exactly the bytes that get bound, shown through one reversible escape.** The command
  rebuilds the dry-run artifact from `(Proposal, RepoView)` and displays the complete, untruncated
  patch through a single **injective, ASCII-only escape grammar** so two different patches can never
  render identically: a literal backslash is shown as `\\`, a **TAB as `\t`** (not an arrow), each
  **trailing space as `\x20`** (so trailing whitespace is visible and countable), and every non-ASCII
  code point as `\u{HEX}`; a header field states **whether the patch ends with a final `LF`**. This is
  display-only — the bytes bound by `patch_hash` / `bound_hash` are the original exact `proposal.diff`
  UTF-8 bytes, never the rendered text. Content that cannot be rendered faithfully — a carriage
  return, a C0/C1 control, an ANSI escape, a bidirectional-format or zero-width character, a Unicode
  noncharacter, or a surrogate — makes the proposal **unapprovable in this version** (it fails closed
  rather than approve a normalized or truncated rendering). Diffs that rely on such characters (for
  example some bidirectional/right-to-left source) therefore cannot be approved through Cofferdam in
  v0.1.
- **A terminal that cannot render the change creates no approval.** Every terminal write goes through
  a helper that turns an encoding or stream failure into a bounded, terminal-safe error with no
  traceback. The complete change and the confirmation prompt must be **written *and* flushed**
  successfully before the confirmation line is read: a checked `flush` runs immediately before
  `_read_confirmation`, and on a fully buffered stream that flush is what actually delivers the bytes,
  so a failed (or unflushable) display aborts **before** any confirmation, ledger lock, or mint — a
  partial display is never followed by an approval, and `.cofferdam/` is not even created. Untrusted
  detail (the target path, OS error text) is never echoed raw: the path is shown through the same
  escape grammar, and error messages are fixed bounded categories rather than raw exception strings.
  Conversely, once the record has been appended and fsynced, a failure to **write or flush** the
  success message does not reverse the approval: the command exits non-zero with the same
  indeterminate/recorded-authority warning (an active approval exists — verify with `approval-status`
  or wait for expiry). An inability to display an *error* never changes the authority decision: the
  exit code is fixed and no mint is retried, even if both stdout and stderr are unusable (no traceback
  escapes either way).
- **The approval binds the state the human saw.** After the typed confirmation, the artifact is
  rebuilt a second time **under the ledger lock**, and the mint proceeds only if the full 64-character
  `bound_hash` still equals the one that was displayed. If the target changed while the human was
  reading, nothing is recorded and the command exits non-zero, asking for a fresh review. `bound_hash`
  is binding only (never authorization) and `approval_id` is a random event identifier, never a bearer
  token.
- **The ledger lock does not freeze repository files.** An approval can go stale the instant after it
  is minted (the working tree can change), and it expires after exactly five minutes regardless. PR3c2
  must therefore independently rebuild the artifact, recompute the binding, and re-check validity
  immediately before it consumes the approval and executes — a stale approval must never authorize
  changed content.
- **A torn mint append fails closed; an unflushed-but-complete append is treated as indeterminate.**
  If a crash tears the approval append (a partial write), the whole ledger is invalidated on the next
  read (no approval is treated as valid) rather than a half-written record being trusted. If instead
  the record was written **completely** but its `fsync` (the durability barrier) then failed, the
  command does **not** claim the approval failed: it exits non-zero with a bounded warning that
  **approval state is indeterminate — an active approval may already exist**, and directs you to run
  `cofferdam approval-status --file <path>` (read-only) or to wait at least five minutes for expiry
  before retrying (a blind retry may report an active duplicate). Cofferdam never auto-truncates or
  rolls back on this condition. The same indeterminate posture applies if the record was written and
  flushed but the success message could not be displayed. Recovery for a genuinely torn ledger is the
  manual procedure below.

## Recovering a corrupt or torn ledger

Cofferdam never auto-truncates or auto-repairs the approval ledger — any malformed line, or a
non-empty ledger that does not end in a complete newline-terminated record, makes the whole file
unusable and every approval invalid (fail-closed). To recover:

1. **Stop all Cofferdam activity.** Ensure no `cofferdam approve` / `approval-status` (or any process
   that opens the ledger) is running, so nothing holds or races the lock.
2. **Inspect** `<repo>/.cofferdam/approvals.jsonl` in a plain text editor. Each line is one
   self-contained JSON record. A corrupt ledger is almost always a **torn final line** (a partial
   record with no trailing newline, from a crash mid-append) or a single malformed line.
3. **Remove only the corrupt content.** Delete the torn/partial trailing line (or the single
   malformed line), leaving every complete, newline-terminated record intact. Do not edit the
   contents of a valid record, and ensure the file still ends with a newline.
4. **Verify** with `cofferdam approval-status --file <proposal.json>` (read-only): it exits `2` while
   the ledger is still unreadable and `0`/`1` once it parses cleanly.
5. If you are unsure, it is always safe to **delete the ledger entirely** — this discards all
   outstanding approvals (each of which expires within five minutes anyway); simply re-run
   `cofferdam approve` to mint a fresh one. Never hand-write an approval record: authority comes only
   from a record the interactive mint created.

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
