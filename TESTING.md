# Testing

> **Scope note (2026-08-01):** this strategy was written for the **Trust Core module** and
> remains binding for it (including its Linux/macOS/Windows/WSL matrix). The workstation
> product targets Ubuntu Desktop only for now; its per-milestone acceptance tests are defined
> in [`ROADMAP.md`](ROADMAP.md) and may add dependencies (e.g. Playwright) outside the Trust
> Core's stdlib-only rule.

## Strategy

Cofferdam's trust boundary is proven by tests, not by dogfooding alone — a passing manual dogfood
run is a **complement** to the test suite, never a substitute for it.

## A skipped test is not a passing test

The workstation tests skip when the `workstation`/`dev` extras are absent, so the Trust Core
suite still runs on a bare interpreter. That convenience creates a real failure mode:
**green-by-skipping.** When workstation code changes, confirm the tests actually ran — check the
skip count, or run the workstation modules directly:

```sh
python -m unittest discover -s tests -t . -p "test_workstation*.py"
```

The same rule governs host behaviour: **stub-adapter results are never platform validation.** An
Ubuntu acceptance run requires `/api/status` to report `adapter: linux-x11` and `stub: false`
(see `docs/checklists/m1-ubuntu-validation.md`).

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

- **PR3c1 (interactive approval mint, non-executing):** the `cofferdam approve` command end-to-end
  with faked TTY streams — a valid approval (exact typed confirmation → one record appended and
  fsynced, target file untouched, no full `approval_id` printed); proposal-input rejection (missing /
  directory / oversized / invalid-UTF-8 / malformed-JSON / invalid-schema / caller-supplied
  `bound_hash` / stdin `-` / no `--file` / unknown flags such as `--yes`/`--force`/`--repo`/
  `--execute`), a proposal file **outside** the repository accepted, a symlinked proposal accepted,
  and the proposal file proven **opened exactly once**; guard `BLOCKED` → exit 2 with no state
  created; the terminal-safety screen (unit tests for CR, C0/C1, DEL, ANSI/ESC, bidi, zero-width,
  Unicode noncharacters and surrogates rejected; combining marks permitted; NFC/NFD never applied) and
  an end-to-end hostile-diff proof that unrenderable content is unapprovable and writes nothing; the
  **injective escaped display** (an actual TAB vs a literal `→`, an actual TAB vs a literal `\t`, one
  TAB vs spaces, consecutive/countable TABs, a trailing space vs none and one vs many, a literal
  `\x20` vs a real trailing space, NFC-composed vs NFD-decomposed non-ASCII, and the explicit
  final-`LF`-yes-vs-no header all produce **different** displays; the whole rendered display is proven
  **pure ASCII**; and the `bound_hash` is proven to derive from the original patch bytes, not the
  rendered text); the TTY gate (stdin/stdout/stderr each required) and one-attempt confirmation
  (exact-phrase accepted; one-character / case / wrong-prefix / leading- and trailing-whitespace /
  oversized / embedded-control mismatches, EOF, and `KeyboardInterrupt` all decline with exit 1 and
  no state before confirmation); the **confirmation byte-limit** (measured in **UTF-8 bytes**, not
  characters: exactly 256 ASCII bytes accepted, 257 rejected, and a sub-256-character but over-256-byte
  multibyte line rejected while a within-256-byte multibyte line is accepted); the **post-confirmation
  recompute under the lock** (a target change during confirmation changes the full `bound_hash` →
  refused, nothing appended; a change to the proposal *file* during confirmation is ignored because the
  immutable in-memory `Proposal` is reused); **terminal-write-and-flush safety** (a stdout
  encoding/stream failure on the display or the prompt aborts **before** any mint with exit 2 and no
  traceback and no `.cofferdam/`; the checked **flush before confirmation** is proven fail-closed — a
  flush `OSError` or a closed-stream `ValueError` before confirmation gives exit 2 with `_mint` never
  called, `stdin.readline` never called, no `.cofferdam/`, and the target unchanged; an event-ordering
  stream proves the confirmation line is read **only after** a successful stdout flush, and a fully
  buffered stream whose flush fails never reaches input; an stderr write **and** flush failure still
  returns an exit code rather than raising; a **post-mint** success-message write **or flush** failure
  returns exit 2 with the indeterminate-authority warning **while the approval exists** and no full
  `approval_id`); the mint core (`approval_id` is 64 lowercase hex, production entropy is
  `secrets.token_hex`, a collision and an entropy failure both fail closed with no retry,
  active-duplicate returns exit 1 and writes nothing, expired/consumed history permits a fresh mint,
  partial writes complete); the **fsync-durability distinction** (a complete write whose `fsync` then
  fails raises `LedgerDurabilityError` — an `OSError` subclass — the record stays discoverable, and the
  CLI reports **indeterminate** state pointing at `approval-status` rather than "not written"; a
  pre-write zero-byte failure is the ordinary fail-closed `ApprovalError`, not indeterminate); **two
  real `spawn` processes** racing the mint (exactly one succeeds) and racing **first-ever** state
  creation from an empty repository (the `FileExistsError` reopen-and-revalidate path); and the
  authority/import boundary (no public `create_approval`/`mint_approval`, `_mint` unexported with
  `approve_command` its only caller and keyword-only DI, `approve_cli` imported only by `cli.py`,
  `secrets`/`token_hex` confined to `approve_cli.py`, and the full flow running with
  `socket`/`subprocess` sabotaged while writing only under `.cofferdam/` and mutating no target).
  Symlink cases skip cleanly where the platform cannot create a symlink.

- **M1.1 (service lifecycle, structural):** `tests/test_service_unit_lifecycle.py` asserts against
  the shipped unit files and source tree rather than runtime behaviour, because the login-loop
  regression was a *declaration* bug that no runtime test would have caught. It fails if a shipped
  unit names `graphical-session.target` in an activating directive
  (`Wants`/`Requires`/`Requisite`/`BindsTo`/`PartOf`/`Upholds`/`WantedBy`/`RequiredBy`), if an
  always-on unit merely orders against it, if a session-scoped unit is `WantedBy=default.target`,
  if anything shipped starts/stops/restarts/isolates that target, or if the session adapter uses
  anything but a read-only query. It also fails on: a unit pinning
  `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY`, or an entry point requiring them; an unbounded or
  sub-1s restart policy; any occurrence of `systemctl --user exit`,
  `loginctl terminate-user`/`terminate-session`, `gnome-session-quit`, broad `pkill`/`killall`, or
  direct process signalling (`os.kill`, `SIGKILL`, `.terminate()`); a secret or wildcard bind in a
  unit or script; and an installer/uninstaller that touches a user configuration tree, dconf, GNOME
  settings, or automatic login. Comment lines are stripped before matching, so a unit may
  *document* the forbidden directives at length without tripping its own guard — and the
  wildcard-bind check parses real string literals with `ast`, so prose forbidding a wildcard is not
  mistaken for one. Alongside it, `tests/test_workstation_bind_wait.py` pins that waiting for the
  private bind address is **bounded** and never widens the bind, and `tests/test_linux_session.py`
  pins session identity: detection reports the current session generation, a stale post-logout
  environment is not trusted even when the compositor socket still exists, and a launch is refused
  if the session ended or changed. All of these are standard-library only, so they run on the
  stdlib-only CI path too.

- **M1.2 (capability accuracy):** `tests/test_linux_x11_adapter.py` pins that a reported
  capability describes the **live session** rather than this process. Every capability test runs
  with `os.environ` patched down to a boot-started daemon's variable-free environment, so an
  implementation that consults its own environment — the M1.2 defect, which advertised
  `screenshot: true` on a Wayland host because `scrot` existed — fails instead of passing by
  coincidence. Covered: a Wayland session with only X11-root tools is `false`; an X11 session
  with `scrot` may be `true`; no verified session is `false` even with every tool installed; the
  same `PATH` yields different answers for different sessions, so tool presence alone cannot make
  a capability true; a session publishing `WAYLAND_DISPLAY` is treated as Wayland even without
  `XDG_SESSION_TYPE`; a stale inherited `DISPLAY` is dropped rather than handed to a capture; and
  capability is recomputed per request across logout and session replacement. The fail-closed
  rules are pinned alongside them: an unavailable capability refuses with a typed
  `adapter_unsupported` **and starts no capture process**, a non-zero tool exit stays a bounded
  `adapter_failed`, and a zero-byte capture is a failure rather than a screenshot.

- **M2A (registry layer semantics):** `tests/test_registry_layer_semantics.py` pins that a
  registry is an **overlay or a definition, never runtime discovery**. It fails if any runtime
  code or shipped script references the committed examples directory (a "helpful" first-run
  seeding step would fill a real machine's UI with devices that do not exist); if a committed
  *overlay* example carries an id or name that does not say `example`; if an application
  *definition* is example-prefixed (definitions name real code-owned concepts, so the rule is
  deliberately inverted there); if a pre-named resource such as `large-monitor`, `personal-opera`,
  or "Büyük monitör" reappears in an example or in the PWA; if a committed profile points at a
  display id that nothing discovered; if a registry schema grows a field that could name an
  executable, command, or argv, or one that could assert liveness (`pid`, `tab_id`, `running`);
  if loading with no files reports an error or **creates a file**; or if the PWA stops saying that
  it is configuration rather than live state and that empty is normal. It also enforces
  D-2026-08-04-7 structurally: no import, shipped argv, or declared dependency may be a
  coordinate-automation or OCR tool. Standard-library only, so it runs on the stdlib-only CI path.

- **M2B (runtime inventory):** seven files, 140 tests, standard-library only so the whole set runs
  on the stdlib-only CI path. The fixtures are *real* rather than mocks that agree with the parser:
  a writable fake `/proc` whose `stat` layout is cross-checked against a live `/proc/self/stat`,
  and an EDID builder emitting byte-correct 128-byte blocks with a valid checksum. A test
  asserting "an absent serial is not invented" is worthless if the fixture cannot produce a block
  that has none.

  - `test_runtime_displays.py` — two connected panels stay two distinct resources; a disconnected
    connector is not a display; internal/external is classified from the compositor's `is-builtin`
    or from the kernel connector type and **never** from a display name that merely says
    "Built-in"; an unreadable EDID leaves the fingerprint and physical size absent rather than
    placeholdered; identity is stable across reads and **survives the same panel moving to another
    connector**; two panels with byte-identical EDIDs stay two resources, both marked weak.
  - `test_runtime_processes.py` — the same PID at a different start time is a different resource,
    and the same PID and start time is the same one; a different boot or host is a different
    resource; a host with no boot identity yields an `unavailable` collection rather than bare
    PIDs; a process exiting mid-scan is omitted without degrading the status while an *unreadable*
    one downgrades to `partial`; a real secret written into a fixture's `cmdline` and `environ`
    never reaches the payload, and no runtime module opens either file (asserted over the AST).
  - `test_runtime_applications.py` — nineteen Opera processes are **one** running application, and
    a second independent launch is a second one; the instance root is chosen by ancestry, not by
    start order; one GNOME launch producing two scopes is one instance while two different
    applications are not merged; Firefox being an available definition produces no instance until
    a Firefox process exists; an application launched outside Cofferdam is discovered; launch
    attribution is three-valued — a GNOME scope is `confirmed_external`, our own transient unit is
    `confirmed_cofferdam`, and a **Cofferdam-started snap that snapd re-parented into a snap scope
    is `unknown`, not falsely external** (regression for the 2026-08-05 live finding); a process
    named `operator` is not matched to `opera`, and a bundled helper called `chromium` does not
    make its host application claim to be Chromium; a shared `dbus.service` and an app-slice
    `.service` are not instance boundaries.
  - `test_pwa_connection.py` — runs the real `web/app.js` inside a stubbed DOM with a fake clock
    (`tests/pwa_harness.js`, skipped when `node` is absent), because the fresh-iPhone bug was
    control flow that never arrived rather than a wrong string a scan could find. Pins that a
    fresh device is offered the token form; that blocked `localStorage` does not kill the boot and
    the user is told the token cannot be remembered; that an onboarded device connects without a
    prompt; that a rejected token and a 4401 socket close both report *authentication*; that a
    status request or socket which never answers times out into `unreachable` with Retry; that
    background reconnects never reset the header to "connecting…"; and that the token appears in
    no URL and no console line in any of the eight scenarios.
  - `test_runtime_presentation.py` — a definition match and a visible desktop entry are both
    `user_facing`; a `NoDisplay`/`Hidden`/autostart entry is `background` and **still returned by
    the collection**; an application whose *name* reads like a daemon but whose entry is visible
    stays user-facing (mutation check against substring classification); a group with no desktop
    entry is `unclassified` rather than promoted or silently demoted; the entry lookup rejects
    traversal and separators and reads only the `[Desktop Entry]` group.
  - `test_runtime_windows.py` — the collection is `unavailable` with a reason naming what was
    tried, never a successful empty list; a per-instance window count is `None` and never `0`; and
    the status vocabulary itself refuses the dishonest shapes, so the rule is not mere convention.
  - `test_runtime_service.py` — GUI-scoped collections are unavailable before login while
    processes stay available to the headless daemon; a replaced session or a logout invalidates
    the cache immediately, however recent it is; one `/proc` walk feeds both the process and the
    application collection; a stub adapter reports unavailable rather than borrowing this
    machine's real data; an overlay adds a label without touching any identity field; a
    `connector_hint` alone never matches; an overlay for a display nothing found creates nothing.
  - `test_workstation_runtime_api.py` — every route rejects an unauthenticated request, a 401
    leaks no host detail, and **no scan runs before the token is checked**; no runtime route
    accepts a write method (asserted against the registered routes rather than by trying verbs);
    an unavailable collection is distinguishable from an empty one over the wire.
  - `test_runtime_pwa.py` — the live view ships no sample resource and no example-registry id; the
    `unavailable` branch is checked **before** the empty branch, because an unavailable collection
    has zero items too; absent values render as "not reported"; the view issues no write request
    and offers no control that does not exist yet; polling is not aggressive and stops on sign-out.

  Several of these are paired with an explicit **mutation check** — the same property asserted
  against a deliberately broken input, so a guard that could never fail is visible as such.
  `test_registry_layer_semantics.py` gains the structural counterpart: `app.js` may not say
  "running", `live.js` may not say "installed — can launch", and neither may call the other's API.

## What is not yet covered

The `git apply` executor (PR3c2) and the audit chain (PR3d) are not yet implemented; the
corresponding coverage targets above (the `git apply` executor and its fixed-argv guarantee, the
audit chain) are backed by tests when those PRs land. PR3c1 deliberately ships **no** executor, no
`git apply`, no subprocess, no Git invocation, and no repository mutation — only the interactive
approval mint that appends a single-use record to the PR3b store.
