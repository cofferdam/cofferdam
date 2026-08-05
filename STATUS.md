# Status

Accurate as of **2026-08-05** (M2B runtime inventory foundation, [`DECISIONS.md`](DECISIONS.md)
D-2026-08-05-2 … -4). Update this file when a category changes, not on every commit.

## Merged (on `main`)

Trust Core module, PR0 → PR3c1:

- Foundation docs, Apache-2.0 license, clean-room provenance/attestation, CI license scan (PR0).
- `cofferdam` CLI skeleton: `--version`, dispatch registry, exit-code convention (PR1).
- Fail-closed proposal schema/parser, path normalization + containment + protected paths,
  read-only repo view, verdict vocabulary (PR2a).
- Deterministic guard (`evaluate(proposal, repo_view)`), strict unified-diff validator,
  byte-stable verdict serialization (PR2b); RepoView root-containment fix + Ubuntu CI (PR2a fix).
- Binding foundation: domain-separated hashing, canonical target resolution, pre-state
  descriptor, immutable dry-run artifact (PR3a).
- Durable single-use expiring approval ledger (PR3b).
- Interactive human approval mint: `cofferdam approve` (PR3c1).

## Preserved on a branch, not merged

- **PR3c2 — Candidate-B byte-exact executor.** Preserved as a WIP commit (`419f90f`) on branch
  `pr3c2-candidate-b-execution`: `executor.py`, `execute_cli.py`, `execstate.py`, `postimage.py`,
  `platform_support.py`, the authoritative `diffcheck` parser, and their tests. **Incomplete and
  unreviewed** — not merged, not on the critical path, and not to be continued unless a task
  explicitly scopes it. Do not rebase or rewrite that branch.

## In progress

- **M1 — remote control skeleton.** Merged to `main` (PR #6, PR #7, PR #8). Backend service
  (auth, status, typed actions, screenshot/open-application/open-URL, WebSocket events), host
  adapter layer (Linux + Windows dev implementations), PWA, JSON persistence, systemd user unit,
  Ubuntu host-setup runbook and validation checklist.

  **Validated on Windows (development host only):** 476 tests pass; the running service returned
  live host status, captured a real 3840×1716 PNG, launched a browser, opened a URL, streamed
  action events over WebSocket, and rendered correctly at phone (375×812) and tablet (768×1024)
  viewports. This proves the architecture and the typed-action path — nothing more.

  **Validated on the real Ubuntu host — in the current logged-in session only** (2026-08-03,
  Ubuntu 26.04, GNOME/Wayland): 506 tests pass; the systemd user service runs and is `enabled`;
  the listener is bound only to the Tailscale address; `/healthz` returns 200; `/api/status`
  rejects an unauthenticated request with 401 and serves an authenticated one with 200; the phone
  reaches the host over the tailnet and authenticates; `open_application` and `open_url` launch a
  real snap-packaged Firefox and fetch a real URL, each confirmed by evidence rather than a
  launcher exit code (PR #8); the Wayland session is reported honestly with `screenshot: false`
  and GUI capabilities gated on a live graphical-session check.

### OPEN RELEASE GATE — M1 post-reboot auto-start is NOT validated

**M1 must not be described as fully validated, reboot-validated, or complete while this gate is
open.** Everything recorded above was observed in a single continuously logged-in session. The
host has not been rebooted since the service was installed, so the following remain **unverified
by observation**:

- the systemd user service starting automatically after a cold boot, through lingering, with no
  human logging in first;
- `tailscaled` coming up before the service needs its address (the service previously died in a
  restart loop with `cannot assign requested address` when the Tailscale address was absent);
- the listener re-binding to the Tailscale address unattended;
- phone-over-tailnet access working after an unattended reboot;
- graphical-session detection reporting `open_application`/`open_url` as **false** before login
  and **true** once a desktop session exists — the linger-before-login path is covered by tests,
  not yet by a real boot.

Known factor: automatic login is **not** enabled on this host (`/etc/gdm3/custom.conf` has no
`AutomaticLoginEnable`), so after a reboot the API is expected to return while GUI capabilities
correctly report unavailable until someone logs in at the desktop. That expectation is untested.

**How to close it:** reboot the host without logging in, run the reboot section of
`docs/checklists/m1-ubuntu-validation.md`, then record the observed result here and in
[`ROADMAP.md`](ROADMAP.md). The reboot is deferred at the user's request because the workstation
is in active use; it is not blocked or waived.

**M2A does not change this gate.** It alters no boot behaviour, no systemd unit, and no bind
logic; the gate stays open and unaffected, and no M2A document may describe M1 as validated.

### M2A — control plane foundation

- **M2A — control plane foundation.** On branch `feat/m2-control-plane-foundation`, not merged.
  Six versioned registries (devices, displays, applications, browser profiles, agent profiles,
  conversation routes) under `$COFFERDAM_HOME/config/registries/` with strict typed models,
  cross-registry reference validation, normalized Unicode/Turkish alias indexes, an atomic writer
  utility, and bounded structured errors; authenticated **read-only** `GET /api/registries` and
  `GET /api/registries/{registry_name}`; `open_url` extended with an optional `browser_profile_id`
  and domain-policy enforcement; bounded Opera detection; read-only registry sections and a
  browser-profile selector in the PWA; architecture documents and the desktop-companion ADR.

  **Validated on the real Ubuntu host** (2026-08-04, Ubuntu 26.04, GNOME/Wayland): 663 tests
  pass; the systemd user service runs the M2A build and is still bound only to the Tailscale
  address; `/api/registries` returns 401 unauthenticated and serves all six registries
  authenticated from `~/cofferdam/config/registries/`; `/api/status` reports
  `applications: ["firefox", "opera"]` — Opera detected live through the bounded code-owned
  candidates (`/snap/bin/opera`, snap 133); an unknown `browser_profile_id` fails closed with
  `browser_profile_invalid` and launches nothing; a malformed one is rejected with 422 before
  execution; and `open_url` with `browser_profile_id: "personal-opera"` on `https://example.com`
  returned `succeeded` with `selection: "explicit-profile"`, `application: "opera"`.

  Evidence that the URL reached the browser: Opera printed *"Opening in existing browser
  session."* and exited 24 (`CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED`), and the
  pre-existing Opera main process was still the same PID afterwards — the request went to the
  session already in use, and no second browser was started and no window was closed. Visual
  confirmation of the tab itself is the user's to make: screen capture is unavailable on this
  Wayland session (`screenshot: false`), and reading Opera's profile directory is out of bounds.

  This validation found and fixed a real defect: a URL handed to an already-running Opera was
  being reported as a failed action (see the CHANGELOG entry on Opera's delegation exit code).

  **What M2A is not:** **no runtime discovery of any kind**, no Raspberry Pi control, Wake-on-LAN
  or power actions, window movement, browser DOM access, web automation, browser extension, agent
  execution, message sending, natural-language planning, desktop application scaffolding, or
  registry write API. Agent profiles are placeholders (`execution_status: not-implemented`) and
  conversation routes are templates; the PWA labels both as such and offers no control that would
  suggest otherwise.

  **Registry semantics corrected before merge.** The registries were first written as though they
  described the machine — they shipped a `large-monitor` named "Büyük monitör", a
  `personal-opera`, and a `fallback-firefox`. Nothing had been discovered: those were labels for
  resources no code had ever looked for, presented in the PWA under "Machine registries". The
  world is now split into three layers (`DECISIONS.md` D-2026-08-04-6) — code-owned
  **definitions**, **runtime resources** (discovered; *not implemented*, milestone M2B), and
  **user overlays** (all a registry file is). Every committed *overlay* example id and name now
  begins with `example`; application **definitions** deliberately keep neutral concept ids
  (`opera`, `firefox`) because those name real code-owned concepts; nothing copies examples into
  `$COFFERDAM_HOME`; and the PWA panel is "Configuration & templates" whose empty state says
  empty is normal and everything still works. Pinned by
  `tests/test_registry_layer_semantics.py`.

- **M1.1 — service lifecycle correction.** Branch
  `fix/workstation-service-login-lifecycle`. Fixes a **login-blocking regression**: the M1 unit's
  `Wants=graphical-session.target`, combined with `WantedBy=default.target` and lingering,
  activated the graphical target at boot with nothing behind it, so GNOME refused every
  subsequent login ("A graphical session is already running!") and bounced back to GDM. Root
  cause verified in the journal across four failing boots against one working control boot.
  Also fixes an unbounded restart storm when the Tailscale bind address is not up yet at boot.

  Adds `docs/SERVICE_LIFECYCLE.md`, a transactional migration installer, an uninstaller that
  doubles as rollback and TTY recovery, and `tests/test_service_unit_lifecycle.py` — structural
  guards so this class of mistake cannot return silently. Recorded as `DECISIONS.md`
  D-2026-08-04-1 and D-2026-08-04-2.

  **Validated on the real Ubuntu host (2026-08-04).** 547 automated tests pass on both CI paths,
  and the required manual gates were observed: logout/login, **two consecutive reboots**,
  pre-login API access with graphical capabilities reported false and actions refused, post-login
  capability recovery with a real browser launch, daemon-restart isolation (GNOME and both open
  browsers survived), and the bounded bind wait firing in production at boot with `NRestarts=0`.
  The failure signature `A graphical session is already running!` occurred 2–3 times per boot on
  the three boots with the old unit and **zero times across all three boots with the corrected
  unit**. See the validation record in [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).

  Still open: a full Tailscale-outage test end-to-end (only the wait logic is verified, in
  isolation).

- **M1.2 — screenshot capability accuracy.** Branch `fix/screenshot-capability-accuracy`.
  Found during M1.1 boot-`0` validation: after login, a daemon started at boot advertised
  `screenshot: true` on a Wayland host because `scrot` was on `PATH`. The Wayland guard read
  `XDG_SESSION_TYPE` from the **daemon's own** environment, which under lingering is empty, so
  the guard silently did not apply. The action itself failed closed
  (`scrot: Can't open X display`, a bounded `adapter_failed`, no black image, no false success),
  making this an advertisement-accuracy defect rather than a capture defect.

  Capability is now derived from the verified graphical session — the same live source that
  already gates GUI actions — and never from the daemon's environment. Recorded as
  `DECISIONS.md` D-2026-08-05-1. **No Wayland screenshot backend was added**: Wayland capture
  remains unavailable on this host, and the flag now reports that truthfully.

  Not yet validated on the live host. The isolated patch and its tests are complete (563
  automated tests pass on both CI paths); live validation is a separate decision, because the
  running service is deliberately still on the M1.1 validation runtime — see
  [`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).

### M2B — runtime inventory

- **M2B — runtime inventory foundation.** On branch `feat/m2b-runtime-inventory-foundation`, not
  merged. The layer M2A deliberately did not have: read-only discovery of what is **actually
  connected and running**, as `cofferdam/workstation/runtime/` — one narrow module per backend,
  each stating the resources it owns, the evidence it uses, its limitations, and its status
  semantics. Authenticated read-only `GET /api/runtime` and `GET /api/runtime/{resource_kind}`; a
  *Live system* panel in the PWA, kept separate from *Configuration & templates*. Documented in
  [`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md).

  Backends, chosen after read-only investigation of the real host and recorded as `DECISIONS.md`
  D-2026-08-05-2: `org.gnome.Mutter.DisplayConfig.GetCurrentState` joined to `/sys/class/drm` for
  displays (**not** `xrandr`, which under Wayland reports XWayland's synthetic layout); `/proc`
  read directly for processes, never opening `cmdline` or `environ`; systemd cgroup scopes for
  application instances; and **no backend at all for windows**.

  **Observed on the real Ubuntu host** (2026-08-05, GNOME Shell 50.1, Wayland) with the live
  service left untouched on its existing validation runtime: the internal panel and one external
  monitor are discovered as **two distinct resources**, each with a real connector, model,
  resolution, refresh rate, physical size and EDID-derived fingerprint, and each classified
  internal/external from the compositor's own `is-builtin` rather than from its name. Opera's
  **19 processes are reported as one running application**, mapped to the `opera` definition; a
  GNOME-launched application whose launch produced two systemd scopes is reported as one instance;
  application groups with no matching definition are reported running and **unmapped** rather than
  guessed. Firefox **is** installed and launchable here (snap 149.0.2-1, resolved at
  `/usr/bin/firefox`, and listed by `/api/status` as an available application); it simply was not
  running, so it correctly produced no instance. Launching it through Cofferdam during the same
  validation made exactly one `firefox` instance appear on the next refresh — 11 processes grouped
  under one card, matched to the `firefox` definition by executable basename. An earlier draft of
  this line read "Firefox is not installed on this host", which was wrong, contradicted the
  `applications: ["firefox", "opera"]` observation recorded above, and reproduced in prose the very
  installed-versus-running conflation this milestone exists to remove.

  That same launch exposed a second, unrelated truthfulness defect, now fixed. Launch provenance
  was a boolean, `launched_by_cofferdam`, and the Firefox that Cofferdam had just started came back
  `false`: snapd re-parents a snap launch into `snap.<package>.<app>-<uuid>.scope`, discarding our
  transient unit before the first scan. The boolean had no way to say "the evidence is gone", so it
  asserted the one thing that was untrue. It is replaced by three-valued `launch_source`
  (`confirmed_cofferdam` / `confirmed_external` / `unknown`); snap launches report `unknown`, and
  the absence of our unit is never on its own grounds for claiming an external launch. See
  [`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md).

  **The phone found a third problem that no test could have caught: the page was true and still
  wrong.** Opera and Firefox sat in the primary list beside three GNOME notification helpers, and
  the process section rendered ~116 rows of systemd, D-Bus and PipeWire ahead of anything a person
  controls. Cofferdam is a workstation control plane, not a system monitor. Discovery and the API
  are unchanged and still complete; instances now carry `presentation` and
  `presentation_evidence`, derived from definition matches and freedesktop desktop-entry metadata
  (`NoDisplay`, `Hidden`, XDG autostart) rather than from names, and the PWA demotes background
  helpers and undecidable groups into collapsed sections, collapses the process inspector behind
  an explicit action with search and per-application filtering, moves technical detail behind a
  second disclosure, and stops advertising a capability the host reports false.

  **Windows are `unavailable`, with a reason** — the honest result, not a stub.
  `org.gnome.Shell.Eval` returns `(false, '')` here (disabled outside unsafe-mode, and barred by
  D-2026-08-04-7 regardless), no portal enumerates windows, and the accessibility bridge is
  switched off on this host. Reporting an empty list would tell a user with three windows open
  that they have none.

  **What M2B is not:** no control of any kind. It starts, stops, moves, reconfigures, and
  terminates nothing. M2B1 accepted no write method at all; **M2B2 adds exactly two** —
  `PUT`/`DELETE /api/runtime/displays/{resource_id}/overlay`, for naming a display. That is
  metadata *about* a resource, not control *of* one. Overlays still resolve onto discovered
  displays on hardware-grade evidence only, and application-instance labels remain future work
  because their identity is boot-scoped. No browser tabs, no agent task inventory,
  no window movement, no GNOME extension, no new dependency.

  905 tests pass on both CI paths (744 before this branch), with zero skips when the workstation
  extras are installed.

  **Unclosed validation gap, inherited by M2B2.** PR #13's logout/login cycle was planned,
  approved, and instrumented — a bounded read-only recorder sampled the service every 10 seconds
  from the user manager, which survives logout — but it never ran. All 214 samples show one
  unchanged graphical session (`gsession-426dede61a51883c`), one unchanged gnome-shell PID, and
  displays `ok` throughout, so no logout occurred before PR #13 was merged. What the recorder
  *does* attest, across every sample: the unit stayed `active` with `NRestarts=0` and an unchanged
  MainPID, `Wants=`/`BindsTo=`/`PartOf=` stayed empty, `/healthz` stayed up, unauthenticated
  `/api/status` stayed 401, screenshot never became true, and windows never became `ok`.
  The lifecycle behaviour at GDM and across a real login therefore remains **unverified on this
  host** for the M2B runtime. M2B2 does not change graphical-session lifecycle behaviour, so it
  does not close this gap and does not require a logout of its own; the cycle should be run once
  to close it.

**M2B does not change the M1 reboot gate.** It alters no boot behaviour, no systemd unit, and no
bind logic.

## Planned (active roadmap — see [`ROADMAP.md`](ROADMAP.md))

- Guardian/Supervisor + manual recovery command surface, Runtime A/B slots, process/window/
  display control, browser/media control (YouTube, Netflix profile), Claude Code task adapter,
  update records, A/B self-update demonstration, natural-language intent routing (Ollama),
  OpenClaw spike.

## Deferred (preserved, not on the critical path)

- Trust Core completion: finishing/reviewing/merging PR3c2, PR3d hash-chained audit log, PR4
  hardening. The module is preserved for future privileged-action and high-assurance-update use.
- Obsidian integration, vector/advanced memory, council/multi-model review integration, voice
  and wake words, native mobile apps, generalized multi-agent orchestration.
- Windows and macOS *host* support for the workstation product.
- Domain/trademark registration and any productization gates.

## Speculative (no commitment)

- Reusing the Trust Core approval boundary for Guardian updates, package installation,
  root-level operations, and destructive migrations.
- Additional client platforms beyond the PWA.

## Superseded

- The v0.2–v0.6 version-line roadmap (provider adapter / Review Room / allowlisted command
  workflow / parity / preferences-lenses / hosted-team-mobile) as a product plan.
- "v0.1 Trust Core is the only committed scope" and "nothing leaves your machine" as
  whole-product claims (they remain true of the Trust Core module).
- Per-PR council review gates as the default process.
- Monetization/productization planning (sponsors, design partners, hosted plans).
