# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and this project does not yet have a stable
release.

## [Unreleased]

### Added

- **Runtime inventory (M2B) — Cofferdam can see what is actually connected and running.** The
  layer M2A deliberately did not have. Read-only discovery lives in
  `cofferdam/workstation/runtime/`, one narrow module per backend, each stating the resources it
  owns, the evidence it uses, its limitations and its status semantics. Full write-up in
  [`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md).

  - **Connected displays** — from `org.gnome.Mutter.DisplayConfig.GetCurrentState` (the
    compositor's own view: layout, scale, orientation, refresh rate, primary, `is-builtin`),
    joined to `/sys/class/drm` for the EDID fingerprint and physical millimetres. Deliberately
    **not** `xrandr`: under Wayland it reports XWayland's synthetic layout, and M1 could only ever
    honestly take a display *count* from it. The two sources are joined on the panel's own
    EDID-derived `(manufacturer, model, serial)` triple, because the kernel says `card1-HDMI-A-1`
    where Mutter says `HDMI-1` — content matching is exact, a hand-maintained name mapping is a
    guess. Display identity is the SHA-256 of the EDID scoped to the host, so a label survives a
    reboot and a cable moved to another port; a panel whose EDID cannot be read gets a
    connector-derived identity explicitly marked `weak`. Manufacturer, model and serial are
    reported exactly as the hardware described itself, and a panel that published no model *name*
    is reported by its product code with `model_source` saying so — nothing becomes "Unknown".
  - **Processes** — `/proc`, read directly. Identity is host + boot + PID + start time, never a
    bare PID: PIDs are recycled within minutes, and `start_ticks` is published so a later control
    action can re-verify it before acting. A host with no boot identity gets an `unavailable`
    collection rather than bare PIDs. A process that exits mid-scan is omitted without degrading
    the collection; one that exists but cannot be read downgrades it to `partial`.
    `/proc/<pid>/cmdline` and `/proc/<pid>/environ` are **never opened** — both routinely carry
    secrets, and not reading them is a far easier guarantee than redacting them.
  - **Running application instances** — grouped by systemd cgroup scope, because the system
    already computed the boundary. Opera's **19 processes are one running Opera**, not nineteen;
    a GNOME launch that produces two scopes for one application is one instance, merged on
    systemd's naming grammar rather than by substring. Mapping to an application definition
    requires the exact basename of the root process's real executable — `operator` is not Opera,
    and an Electron application bundling a `chromium` binary does not become Chromium. No match
    leaves the instance running and **unmapped**, which is a complete answer.
  - **Windows** — the interface exists and is wired into the snapshot; **no safe read-only backend
    is available on GNOME Wayland**, so the collection reports `unavailable` with a precise
    reason. `org.gnome.Shell.Eval` returns `(false, '')` on this host and would be arbitrary code
    execution inside the compositor anyway; no portal enumerates windows; the accessibility bridge
    is switched off and enabling it is the user's decision. An empty list would tell a user with
    three windows open that they have none. The seam for a user-installed GNOME companion is
    documented.

  Collection status is a closed vocabulary — `ok` / `partial` / `unavailable` / `error` — and the
  model *enforces* it: an `unavailable` collection that carries items, or omits a reason, raises.
  An `ok` collection with zero items is a positive claim that the machine has none of that
  resource. Recorded as `DECISIONS.md` D-2026-08-05-2, -3 and -4.

- **Authenticated read-only runtime API.** `GET /api/runtime` serves one snapshot —
  `observed_at`, host/boot/session identity, and the four collections — and
  `GET /api/runtime/{resource_kind}` serves one slice of it together with that header, because a
  list of processes is uninterpretable without the boot it was read in. Sub-endpoints slice a
  shared snapshot rather than scanning independently, so a client can never assemble a picture
  whose displays came from one instant and whose processes came from another. A short cache keeps
  a polling phone from driving a continuous process scan, and is invalidated by *identity* as well
  as by time: a replaced graphical session drops it however recent it is. `?refresh=true` bypasses
  it. **No route accepts a write method** — process and window control is a later milestone with
  its own identity re-verification rules.

- **A "Live system" area in the PWA**, in its own `web/live.js`, separate from *Configuration &
  templates*. The separation is structural so each file can be checked for the vocabulary the
  other must never borrow: `app.js` renders definitions and may not say "running", `live.js`
  renders runtime resources and may not say "installed — can launch". This closes the M2A
  live-validation finding that the card reading *Firefox available* was taken to mean Firefox was
  open. An `unavailable` collection renders the backend's reason, and that branch is checked
  *before* the empty branch — an unavailable collection has zero items too. Values the host did
  not report render as "not reported"; window counts are absent rather than zero. Cards are
  compact and expand on tap; polling is conservative, pauses while the page is hidden, and stops
  on sign-out.

- **`HostAdapter.application_executables()`** — a read-only view of the adapter's own launch table,
  so runtime discovery can map a process group to a definition without hardcoding a program name.
  It deliberately does not follow `/snap/bin/opera` to its symlink target `/usr/bin/snap`, which
  would classify every unrelated snap helper as Opera.

### Fixed

- **Launch provenance claimed a fact it could not prove, for every snap application
  (2026-08-05).** Found during PR #13 live validation on the real Ubuntu host. Cofferdam issued
  `open_application` for Firefox; the instance was discovered and grouped correctly, and reported
  `launched_by_cofferdam: false` — about a launch Cofferdam had just performed. Snapd re-parents
  every snap launch out of our `cofferdam-app-<hex>.service` into
  `snap.<package>.<app>-<uuid>.scope` before the first scan, so the evidence is gone by then. A
  boolean has no way to express "cannot be determined", so it asserted the one reading that was
  definitely untrue: that something else had launched it. Opera was equally affected.

  The boolean is replaced by three-valued `launch_source` — `confirmed_cofferdam`,
  `confirmed_external`, `unknown`. Snap scopes report `unknown` unconditionally, and the absence
  of our transient unit is never on its own grounds for `confirmed_external`: that state requires
  a launcher to have named *itself* in the unit (`app-gnome-<AppID>-<pid>.scope`), a shape
  Cofferdam cannot produce because `systemd-run --user --unit=` creates a `.service`. The PWA
  badges only the two confirmed states and renders `unknown` as "launch source not confirmed",
  never as "not launched by Cofferdam". Regression test covers a Cofferdam-started snap moved into
  a snap scope.

- **A live-validation report said Firefox was not installed on this host; it is (2026-08-05).**
  `STATUS.md` recorded "Firefox is not installed on this host and correctly produces no instance."
  Firefox is installed and launchable — snap 149.0.2-1, resolved at `/usr/bin/firefox` from the
  daemon's own `PATH`, and already listed by `/api/status` as an available application in the same
  document. It was merely not *running*. The sentence reproduced in prose exactly the
  installed-versus-running conflation this milestone exists to remove. Corrected against live
  evidence: launching Firefox through Cofferdam produced one `firefox` instance, 11 processes
  grouped under one card, matched by executable basename. No discovery-code defect was involved.

- **Screenshot capability was over-advertised in a Wayland session (2026-08-05).** After login,
  a daemon started at boot by lingering reported `screenshot: true` on a GNOME Wayland host
  because `scrot` was on `PATH`, and the phone enabled a Screenshot button whose action could
  only fail (`scrot: Can't open X display`). The guard that rejects X11 root-capture tools under
  Wayland was reading `XDG_SESSION_TYPE` from the **service's own** environment, which under
  lingering is empty — GNOME populates the user *manager* at login, not an already-running
  process — so the guard silently never applied. The action failed closed throughout (bounded
  `adapter_failed`, no black image, no false success), so this was an advertisement-accuracy
  defect, not a capture-correctness one. Capability is now derived from the verified graphical
  session returned by `detect_graphical_session()`, which also carries the session's live
  environment; a capture runs with that session's display variables, and a stale
  `DISPLAY`/`WAYLAND_DISPLAY` inherited from an ended session is dropped rather than passed on.
  A session publishing `WAYLAND_DISPLAY` counts as Wayland even without `XDG_SESSION_TYPE`.
  **No Wayland screenshot backend was added** — Wayland capture remains unavailable on this
  host and the flag now says so truthfully. Recorded as `DECISIONS.md` D-2026-08-05-1.
- **Ubuntu graphical login loop caused by the workstation service (2026-08-04).** Enabling
  `cofferdam-workstation.service` made GNOME unable to complete a login: the password was
  accepted, the desktop began to load, and the session died back to GDM — every time. The unit
  declared `Wants=graphical-session.target` while being `WantedBy=default.target` on a host with
  `loginctl enable-linger`. Lingering starts the user manager at boot; `default.target` pulled
  the service in; and `Wants=` **activated** `graphical-session.target` with no compositor behind
  it. gnome-session then found the target it is itself supposed to activate already active,
  refused with "A graphical session is already running!", and quit. Confirmed against the journal
  across four failing boots versus one working control boot. The unit no longer references
  `graphical-session.target` in any form; session detection was already a read-only query and
  stays one. Recorded as `DECISIONS.md` D-2026-08-04-1. Full analysis, migration, rollback, and
  TTY recovery in [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).
- **Restart storm when the Tailscale address was not up yet (2026-08-04).** The daemon binds
  directly to its private address, which frequently does not exist yet when lingering starts it
  at boot; the bind failed, the process exited, and `StartLimitIntervalSec=0` disabled the rate
  limiter, so it respawned every 3s indefinitely. It now waits for the address, bounded by
  `COFFERDAM_BIND_WAIT_SECONDS` (default 120), then exits cleanly. The unit's restart policy is
  bounded (10 attempts / 5 minutes). The service still never falls back to a wildcard bind.

### Added

- **M2A — control plane foundation (2026-08-04).** Cofferdam gains a vocabulary for the machines,
  displays, applications, browser profiles, agents, and routes it is allowed to talk about.
  - **Six versioned JSON registries** under `$COFFERDAM_HOME/config/registries/` — `devices`,
    `displays`, `applications`, `browser_profiles`, `agent_profiles`, `conversation_routes` —
    with strict typed models, cross-registry reference validation, stable ASCII kebab-case IDs,
    normalized Unicode alias indexes, safe empty defaults, an atomic writer utility, and bounded
    structured errors. Machine registries are never committed; committed placeholders live in
    `examples/registries/`. Standard-library only: no database, no YAML, no new dependency.
  - **Alias resolution** folds Unicode case, trims and collapses whitespace, and folds Turkish
    dotted and dotless I together, so "MONİTÖR"/"monitör" and "IŞIK"/"ışık" match. Duplicate
    normalized names or aliases inside one registry are a validation failure, and the resolver
    returns no match rather than choosing between candidates.
  - **Read-only registry API:** `GET /api/registries` (per-registry version, counts, load status)
    and `GET /api/registries/{registry_name}`, behind the same device token as every other
    state-revealing route. There is no `POST`/`PUT`/`PATCH`/`DELETE` registry endpoint in M2A.
  - **`open_url` gained an optional `browser_profile_id`.** An explicit profile selects its
    application and never falls back to another; domain policy is enforced before launch; an
    unavailable browser reports `application_unavailable`. With no profile given, the single
    enabled `default_for_url` profile is used when its browser is available, otherwise the
    pre-M2A legacy launch is preserved exactly. A URL-only request on a machine with no
    registries behaves exactly as it did before.
  - **Opera** joined the code-owned application allowlist, detected through bounded executable
    names (`opera`, `opera-stable`) and desktop-entry basenames. No executable path, argv,
    command, desktop-file path, profile directory, or credential is representable in any schema.
  - **PWA:** read-only cards for all six registries with loading/empty/invalid/unavailable
    states, agent profiles labelled "not implemented", conversation routes labelled "template
    only", and a browser-profile selector on Open URL. No Start/Send/Run/Route control exists.
  - **Docs:** `docs/CONTROL_PLANE.md`, `docs/DEVICE_REGISTRY.md`, `docs/APPLICATION_PROFILES.md`,
    `docs/AGENT_ROUTING.md`, and `docs/DESKTOP_APP.md` (an ADR comparing an installed PWA, a
    Tauri 2 thin shell, and Electron — recommending a thin Tauri companion, with no scaffolding
    added in M2A). Decisions recorded as `DECISIONS.md` D-2026-08-04-3..5.
  - **Registries are overlays, not runtime discovery.** They were first written the wrong way
    round: the committed examples shipped a `large-monitor` named "Büyük monitör", a
    `personal-opera`, and a `fallback-firefox`, and the PWA presented them as "Machine
    registries". Nothing had been discovered — those were labels for resources no code had ever
    looked for, and a browser profile read as though it meant an open browser. The product now
    separates **definitions** (code-owned: which applications exist as a concept), **runtime
    resources** (connected displays, running processes, application instances, windows —
    **not implemented**, milestone M2B), and **user overlays** (optional labels, aliases,
    preferences: all a registry file is). Consequences: every committed overlay example id and
    name begins with `example`; application definitions keep neutral concept ids (`opera`,
    `firefox`) because they name real code-owned concepts; no code path, shipped script, or
    first-run step copies examples into `$COFFERDAM_HOME`; a machine with no registry files is
    fully working; and the PWA panel became "Configuration & templates", with per-section titles
    naming each layer and an empty state reading "Nothing configured — this is normal, and
    everything still works". Recorded as `DECISIONS.md` D-2026-08-04-6, with D-2026-08-04-7
    adding the semantic-interfaces-only rule — no pixel-coordinate automation, and Cursor as a
    future *target-agent adapter* rather than a route into an existing ChatGPT conversation.
    Pinned by `tests/test_registry_layer_semantics.py`.

  M2A implements no runtime discovery of any kind, and no Raspberry Pi control, Wake-on-LAN or
  power action, window movement, browser
  DOM access, web automation, browser extension, agent execution, message sending,
  natural-language planning, or desktop application scaffolding — and changes no reboot
  behaviour. **M1's post-reboot validation gate remains open.**
- **Service lifecycle documentation and enforcement (2026-08-04):**
  [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md) separates directly observed facts from
  supported interpretation and unproven assumptions, and documents daemon behaviour before,
  during, and after login, at logout, and across repeated logins.
  `deploy/install-workstation-service.sh` performs a transactional, idempotent migration
  (inventory → back up Cofferdam-owned files → disable the old enablement path → install →
  verify → enable) and refuses to enable a unit that names `graphical-session.target`.
  `deploy/uninstall-workstation-service.sh` is the rollback and TTY-recovery path; it resolves
  every symlink before unlinking, so it can only ever remove its own.
  `tests/test_service_unit_lifecycle.py` fails if any unit pulls, starts, or stops the graphical
  target; if a prohibited session-termination command or a broad `pkill`/`killall` appears; if a
  restart policy is unbounded; if a unit embeds a secret or a wildcard bind; or if an installer
  touches unrelated user configuration.
- **Session identity carried from detection through to launch (2026-08-04):** GUI actions record
  the graphical session generation they were authorised against, and are refused if the session
  ended or changed before the application starts — so a request can never be delivered into a
  different session after a logout/login.

- **Open-source readiness (docs only, 2026-08-01):** `CONTRIBUTING.md` (development setup,
  worktree workflow, action/adapter proposal rules, platform-evidence expectations, review
  depth, and the dependency policy), minimal GitHub issue templates (bug, Ubuntu validation
  report, adapter/action proposal), and a pull-request template. A license and provenance audit
  confirmed Apache-2.0 is unambiguous across `LICENSE`, package metadata, and CI; that nothing is
  vendored; and that no upstream code is present — recorded as `DECISIONS.md` D-2026-08-01-8.
  `.gitignore` hardened against runtime secrets, screenshots, browser profiles, and repository
  bundles. `SECURITY.md` gained a maturity statement and the M1 workstation posture.

### Changed

- **Direction pivot (docs only, 2026-08-01):** Cofferdam is now an open-source, personal,
  always-on AI workstation and remote computer-control system for Ubuntu Desktop, controlled
  from phone/tablet via a Cofferdam-owned PWA, with a Guardian-supervised A/B self-update
  loop. The Trust Core is preserved off the critical path for future privileged-action use.
  New `DECISIONS.md`, `STATUS.md`, `ROADMAP.md`, `AGENTS.md`, `CLAUDE.md`; rewritten
  `README.md`/`DESIGN.md`; scope notes added to the Trust Core docs. No code changes.

### Fixed

- **Opening a URL in an already-running Opera was reported as a failure** (M2A Ubuntu
  validation, snap-packaged Opera 133). Launching `opera <url>` while Opera is running prints
  "Opening in existing browser session.", opens the tab, and exits **24** — Chromium's
  `CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED`. systemd marks any non-zero exit as `failed`,
  so the adapter called a tab that had visibly opened "the application exited immediately instead
  of starting". The launcher now accepts a **per-application list of specific** delegation exit
  codes (`{"opera": (24,)}`), and such an exit is still reported as `exited` — never as running.
  The M1 rule is unchanged and still enforced: an exit code alone is never evidence, so the launch
  only succeeds when a live instance of the same application can also be seen. Every other exit
  status, and every other application, still fails closed.
- **Graphical actions were reported as succeeded while nothing opened** (M1 Ubuntu validation,
  GNOME/Wayland, Ubuntu 26.04). `open_application` and `open_url` returned `succeeded` with a PID,
  but no window ever appeared. Two independent defects combined. First, the service runs with
  `NoNewPrivileges=yes`, which drops file capabilities across `execve` for every process it forks;
  Ubuntu's Firefox is a snap whose `snap-confine` needs permitted capabilities, so every launch died
  instantly with `snap-confine is packaged without necessary permissions`. Second, the adapter
  spawned the child and returned its PID without ever waiting, so that failure was invisible —
  and `xdg-open` hid it a second way, exiting 0 after delegating whether or not a browser ever
  started. The adapter now hands each application to the **systemd user manager** as a transient
  unit (`systemd-run --user`), which is not subject to the service's `NoNewPrivileges`, gives the
  application its own cgroup (restarting Cofferdam no longer kills the user's browser), and lets it
  inherit the manager's *current* session environment — so a service started by lingering before
  graphical login still launches into the session created later. Every launch is now confirmed
  before it is reported: the process must survive a settle window, or an existing instance of the
  same application must be visible; otherwise the action fails closed with a structured error.
  `open_url` launches an allowlisted browser directly instead of `xdg-open`, because `xdg-open`
  yields no verifiable outcome. The service's hardening is unchanged, and the fixed-argv boundary
  is unchanged (no caller text ever becomes a command).
- **Status now reports what this host can currently do.** `/api/status` capabilities
  (`screenshot`, `open_application`, `open_url`) are gated on a live check that an active
  graphical session exists — `graphical-session.target` plus a compositor/X socket that really
  exists — rather than on the service's own start-time environment, which is stale or empty on a
  lingering host. `session_type` comes from the same live source. GUI actions fail closed with
  `adapter_unsupported` when there is no session, and the PWA disables every control whose
  capability is false.
- **Corrected the Wayland guidance in the UI and docs.** The status note told users to "log in
  with 'Ubuntu on Xorg' for full support" — misleading, since Wayland runs application and URL
  launches correctly and no Xorg path was validated for screen capture. The note now states only
  what was observed: screen capture is unavailable in this session, launching is unaffected. The
  host runbook and the M1 checklist no longer instruct an Xorg login. Also moved
  `StartLimitIntervalSec` into `[Unit]`, where systemd actually reads it.
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
- Interactive human approval mint (PR3c1): the **`cofferdam approve --file <proposal.json>`** command
  (`approve_cli.py`) — the **only supported path that creates authoritative approval state**. It
  rebuilds the exact dry-run artifact from `(Proposal, RepoView)`, screens the target and patch for
  terminal-unsafe characters (rejecting CR, C0/C1 controls, DEL, ANSI escapes, bidi-formatting,
  zero-width, Unicode noncharacters, and surrogates — such proposals are unapprovable in v0.1),
  displays the **complete patch through one reversible, injective, ASCII-only escape grammar** — a
  literal backslash as `\\`, a TAB as `\t`, each trailing space as `\x20`, every non-ASCII code point
  as `\u{HEX}`, and a header field stating whether the patch ends with a final `LF` — so two different
  patches can never render identically (the bytes bound are the original `proposal.diff` UTF-8, never
  the rendered text). It shows the full 64-hex `bound_hash` and — only when stdin, stdout, and stderr
  are all TTYs — requires the human to type `APPROVE <first 12 hex of bound_hash>` exactly, once (the
  confirmation line is capped at **256 UTF-8 bytes**). On success it **rebuilds the artifact a second
  time under the ledger lock**, requires the full 64-hex `bound_hash` to match what was displayed (so a
  repository change during confirmation fails the approval), then appends one record with a
  `secrets.token_hex(32)` `approval_id`, `created_at` from `SystemClock`, and a fixed 300-second TTL,
  and fsyncs it. Exit codes: `0` recorded, `1` declined/mismatch/EOF/interrupt/already-active, `2`
  usage/non-TTY/input/guard/render/state-change/ledger error. Terminal writes go through a helper that
  turns an encoding/stream failure into a bounded, terminal-safe error with **no traceback**: the
  complete change and prompt must be **written and flushed** (a checked `flush` runs immediately before
  the confirmation is read, so a fully buffered stream whose flush fails aborts **before** any mint),
  and untrusted paths and raw exception strings are never echoed. A post-mint success-message write or
  flush failure keeps the indeterminate-authority posture (the approval already exists). If the record is written completely but its `fsync` then
  fails (or the record is flushed but the success message cannot be shown), the command exits `2` with
  a bounded **"approval state is indeterminate"** warning that points at `cofferdam approval-status`,
  rather than falsely reporting that no approval exists (`LedgerDurabilityError`, an `OSError`
  subclass; Cofferdam never auto-truncates or rolls back). There is **no** public
  `create_approval`/`mint_approval` Python API and **no** non-interactive path (`--yes`/`--force`/
  `--repo`/`-`/stdin-proposal/config/env are all absent); the internal mint seam takes no
  caller-controlled clock/store/entropy/TTL/`approval_id`/`bound_hash`. `approve` **executes nothing**:
  no `git apply`, subprocess, Git invocation, staging, committing, or proposal-target mutation — it
  writes only its own `.cofferdam/` state. First-ever concurrent state creation is hardened
  (`_ensure_dir`/`_ensure_lockfile` catch and re-validate a lost `FileExistsError` race) and covered by
  a real two-process regression, as is concurrent minting (exactly one of two racing processes
  succeeds). **Deliberately still absent (deferred): the byte-exact executor (PR3c2) and the
  hash-chained audit log (PR3d).**
