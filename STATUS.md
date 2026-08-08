# Status

Accurate as of **2026-08-08** (M2F Agent Task Core and M2G the Claude Code adapter merged; the
isolated Custom GPT Actions mobile probe passed; client architecture and the active roadmap
recorded as [`DECISIONS.md`](DECISIONS.md) D-2026-08-08-1 … -6). Update this file when a category
changes, not on every commit.

## Merged (on `main`)

- **M2G — Claude Code adapter** (PR #21, squash-merged as `267fae9`). The first adapter that runs
  a real program, on the M2F foundation: one long-lived `claude -p` process per task, a bounded
  stream-json parser, process identity re-verified before every signal, and no Bash tool in the
  profile at all. Off by default behind `--enable-claude-code-adapter` and a per-project
  permission. Detail in the milestone record below, and in
  [`docs/CLAUDE_CODE_ADAPTER.md`](docs/CLAUDE_CODE_ADAPTER.md).

  **Validated live from the phone before merge**, against the real host and the installed CLI: an
  initial delegated task, a same-session follow-up, turn completion, polling, draft preservation
  while polling, duplicate suppression, finish and slot release, cancellation isolated to one
  task, a repeated cancel answered as a conflict rather than a second signal, restart reported as
  `interrupted` rather than resumed, orphan cleanup, isolation from unrelated Claude sessions on
  the machine, and no task content in the broad logs. Those behaviours are the parity list the
  Agent SDK adapter must meet in M2I before this one is retired.

- **M2F — Agent Task Core foundation** (PR #20, squash-merged as `9a645eb`). The provider-neutral task model that had to exist before any agent: identity, an eleven-state machine committed in one transaction with its event, a SQLite store, an append-only history, a host-owned project registry, the adapter protocol, and a deterministic validation adapter that runs no program. Eight authenticated routes and a **Tasks** panel. A default install after this merge has an empty adapter list, which is the honest state of a foundation. See [`docs/AGENT_TASK_CORE.md`](docs/AGENT_TASK_CORE.md) and D-2026-08-06-2.

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

## Verified outside this repository (capability probes)

- **Private Custom GPT Actions, from a real iPhone — PASSED 2026-08-08.** An isolated echo
  service, in its own directory outside this repository with its own port and its own bearer
  credential, behind a temporary Cloudflare Quick Tunnel. The desktop GPT Builder called
  `GET /health` and then the consequential `POST /echo` successfully; the **native iPhone ChatGPT
  application** then made the same two calls successfully, and the `POST` response came back into
  the same private conversation. The mobile request carried `client_test_id = mobile-app-1`, and
  the server recorded `seen = 1`, `duplicate = false` — one accepted mobile confirmation, exactly
  one server invocation.

  **The working tunnel needed `--edge-ip-version 4` and `--protocol http2`:** the phone's hotspot
  allowed Cloudflare region2 over IPv4 TCP 7844, while region1, IPv6 and QUIC were unavailable.
  That is a recorded transport observation, not a solved deployment.

  **What this establishes:** a private Custom GPT can reach a personal workstation service from a
  real phone, with bearer authentication, including an action that requires confirmation.
  **What it does not:** any production behaviour. **No Cofferdam Action exists** — `create_task`,
  `get_result` and the rest are M2I.5, unimplemented. Production transport and network reliability
  are still that milestone's problem. The Cofferdam repository, the workstation daemon, the PWA,
  Task Core and the systemd configuration were untouched and unexposed by the probe, and no probe
  credential is in this repository. Decided as D-2026-08-08-1.

## Preserved on a branch, not merged

- **PR3c2 — Candidate-B byte-exact executor.** Preserved as a WIP commit (`419f90f`) on branch
  `pr3c2-candidate-b-execution`: `executor.py`, `execute_cli.py`, `execstate.py`, `postimage.py`,
  `platform_support.py`, the authoritative `diffcheck` parser, and their tests. **Incomplete and
  unreviewed** — not merged, not on the critical path, and not to be continued unless a task
  explicitly scopes it. Do not rebase or rewrite that branch.

## Milestone records (all merged; written while each was on its branch)

Each entry below was written as its milestone was built and is kept as that milestone's record.
**They are all merged on `main` now** — the merge reference is on each heading — so where an entry
says "on branch, not merged" it is describing the moment it was written, corrected in place. What
each milestone *did not* do, and which validations are still outstanding, is still current and is
why these records are kept rather than collapsed into one line.

Nothing is in progress in this repository today. The queued work is M2H → M2I → M2I.5 → M2J; see
[`ROADMAP.md`](ROADMAP.md).

- **M2G — Claude Code adapter.** Merged as PR #21. The first
  adapter that runs a real program, built on the merged M2F foundation. A phone picks an approved
  project, picks Claude Code, writes a prompt, watches truthful progress, reads the result, sends a
  follow-up to the same live session, and cancels that one task. Adds
  `cofferdam/workstation/tasks/adapters/claude_code/` — the fixed CLI vocabulary, a bounded
  stream-json parser, one identified process per task, and Cofferdam's own Git observations —
  plus the `--enable-claude-code-adapter` gate. Documented in
  [`docs/CLAUDE_CODE_ADAPTER.md`](docs/CLAUDE_CODE_ADAPTER.md) and decided in M2G.

  **Off by default, and there is no shell behind it.** The adapter is registered only when the host
  explicitly enabled it *and* a project in `task-projects.json` permits it. The tool profile omits
  Bash entirely rather than gating it, `--strict-mcp-config` with no config ignores every MCP server
  on the machine, and `--setting-sources ""` means no settings file can widen the profile
  afterwards. `--dangerously-skip-permissions` is never passed.

  **Verified against the installed CLI (2.1.221), not from memory.** Two probes chose the
  architecture: one long-lived `-p --input-format stream-json` process per task, which is what makes
  a follow-up provably reach the same session and a cancellation target a real pid. Process identity
  is pid + `/proc` start time + process group + run id, re-verified before every signal. Exit code
  zero without a result frame is a failure, not a completion. Restart marks the task `interrupted`
  and resumes nothing. 134 focused tests, 17/17 mutations caught.

  **Since validated on the real host, from the phone** — the thirteen behaviours listed under
  *Merged* above, run against the live service before PR #21 was merged. The sentence that stood
  here said the `96-agent-task-core-validation` drop-in was prepared and not applied and that the
  live service still ran the M2E build; that was true while the branch was open and is no longer.

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

**It is now a gate inside M2H.** Every milestone since M1 has been able to say truthfully that it
changed no boot behaviour, which is why the gate could keep being deferred. M2H supervises native
Remote Control hosts as user services and is the first work that changes what runs at boot, so its
unattended-reboot validation and this gate are the same step, and M2H is not complete while it is
open.

**M2A does not change this gate.** It alters no boot behaviour, no systemd unit, and no bind
logic; the gate stays open and unaffected, and no M2A document may describe M1 as validated.

### M2A — control plane foundation

- **M2A — control plane foundation.** Merged as PR #9.
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

- **M2B — runtime inventory foundation.** Merged as PR #13, with M2B2 as PR #14. The layer M2A deliberately did not have: read-only discovery of what is **actually
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

### M2B3A — media and application launch profiles

- **M2B3A — media and application launch profiles.** Merged as PR #15. A code-owned provider catalogue
  (`cofferdam/workstation/media.py`) covering Spotify, YouTube, Netflix, Prime Video and TV+; two
  typed actions (`open_media_provider`, `search_media_provider`); a read-only
  `GET /api/media/providers`; and a *Media* section in the PWA, kept separate from both *Live
  system* and *Configuration & templates*.

  Spotify is the real installed snap application, reached through its own registered `spotify:`
  scheme. The four web services open in Opera as ordinary dedicated windows — **no unofficial
  wrapper was installed or is required**, and Opera's build on this host exposes no `--app` switch,
  so no app-mode window is claimed. Opera also becomes Cofferdam's default browser for generic
  links; the OS default and file associations are untouched, and Firefox stays explicitly
  selectable through a profile or the new `browser_id` field.

  No action claims playback: every media result reports `playback: not_started` on success. TV+
  exposes **Open only** — its unqualified search address discards the query — and says why.

  1,094 tests pass with the workstation extras installed (1,035 before this branch, of which 8 were
  updated where they encoded the pre-M2B3A default-browser behaviour).

  **Not in this milestone:** closing, restarting or terminating application instances (M2B3B);
  Spotify OAuth or Web API playback control; automatic Netflix/Prime/TV+ playback; DOM automation;
  browser extensions; Agent Task Core. The future Spotify and browser-companion adapter seams are
  documented, not built, in [`docs/MEDIA_PROFILES.md`](docs/MEDIA_PROFILES.md).

  **The M2B validation gap above is inherited unchanged.** M2B3A alters no graphical-session
  lifecycle behaviour and does not close it.

### M2B3A.1 — official-provider search and result selection

- **M2B3A.1 — real results you can pick from, for Spotify and YouTube.** Merged as PR #16.
  Official catalogue search through the Spotify
  Web API and the YouTube Data API v3, as `cofferdam/workstation/mediasearch/` — credentials,
  transport, per-provider adapters, a versioned result model, and bounded search sessions. Two typed
  actions (`find_media_results`, `open_media_result`), two routes under `/api/media/`, a
  status-word-only `/api/media/diagnostics`, and result cards in the PWA.

  **The client never names a destination:** search returns opaque handles, and the server
  re-resolves a chosen result from its own bounded session and rebuilds the launch target itself.
  **Credentials never leave the host** — a 0600 file beside the device token, no PWA form, and
  status words as the entire diagnostic surface. **Nothing claims playback**, and Spotify playback
  control is unreachable by construction rather than by restraint.

  Netflix, Prime Video and TV+ are unchanged and structurally cannot gain structured search.

  1,191 tests pass on both CI paths (1,095 before this branch), including six mutation checks that
  prove the safety guards are load-bearing. One real defect was found by driving the PWA rather than
  the API — the phone omitted `provider_id` on open, so every selection failed while every unit test
  passed — and it now has a regression test exercising the client's exact payload.

  **Provider credentials are not configured on this host yet**, so the live provider validation in
  [`docs/MEDIA_RESULTS.md`](docs/MEDIA_RESULTS.md) is still outstanding; the unconfigured path was
  verified end to end instead.

  **Not in this milestone:** Netflix/Prime/TV+ result parsing, Opera Companion, DOM automation,
  Spotify playback or device control, a persistent auto-open-first preference, M2B3B, Agent Task
  Core.

### M2C — audio control foundation

- **M2C — turn the volume down from the phone, and be told the truth about it.** Merged as
  PR #17. A focused `cofferdam/workstation/audio/` module over
  PipeWire 1.6.2 / WirePlumber 0.5.13, read through `pw-dump` and driven through `wpctl` — outputs,
  streams, a versioned snapshot, three typed actions, and four routes under `/api/audio/`. An Audio
  panel in the PWA with a bounded slider, mute, and a collapsed outputs list.

  **These are the first routes that change the physical machine.** A client may send a runtime
  resource id, an integer percentage, and a boolean; there is no field for a node id, a device name,
  a PipeWire property, a command or a program. **A PipeWire node id is never an identity** — it is
  reused after its object is destroyed — so an output is addressed by a digest over host, audio
  graph cookie and stable node name, and the node's name *and* PipeWire serial are re-verified
  against a fresh graph read immediately before acting.

  **No action reports success it has not observed.** `wpctl` exits zero for a command it accepted;
  accepted is not applied, so every action re-reads the host and compares. `requested` and
  `observed` are separate keys in every response.

  **`move_audio_stream` is published as `unavailable` with its reason, not implemented** —
  WirePlumber offers no command for it, and the metadata workaround would address a stream by its
  transient node id and leave that application pinned to that output for future sessions.

  **What is playing is never read.** Stream fields are an allowlist, so `media.name` — the track or
  video title — cannot leak. An application is named only through `pipewire.sec.pid`, the daemon's
  kernel-verified peer credential, resolved through `/proc` to an exact executable match; anything
  else stays unclassified with a reason.

  1,303 tests pass on both CI paths (1,191 before this branch), including six mutation checks
  covering stale resource acceptance, node-id reuse, values above 100, unverified success, false
  stream movement, and arbitrary backend command acceptance. One real defect was found by running
  the code against this host rather than against fixtures: `pw-dump` publishes `Metadata` properties
  at the top level with no `info` key, so reading only `info.props` found no default sink and
  reported a machine with a working speaker as having no default output.

  **This host currently exposes exactly one usable output** — the internal speaker. The NVIDIA HDMI
  card sits at profile `off` with every HDMI route reporting `available=no`, so live validation of
  *switching* outputs needs a second one connected first.

  **Partially validated on the real Ubuntu host** (2026-08-05, PipeWire 1.6.2 / WirePlumber 0.5.13,
  GNOME Wayland) under the `80-audio-control-validation` drop-in: the service runs the M2C build,
  still bound only to the Tailscale address, `NRestarts=0`; `/healthz` returns 200; `/api/audio`
  returns 401 unauthenticated and, authenticated, reports the real default output
  (*Raptor Lake-P/U/H cAVS Speaker*, `builtin_speaker`, route *Speaker*) at **95%**, matching
  `wpctl get-volume` exactly; the HDMI card's absence is explained rather than omitted; the running
  Spotify client is identified through kernel-verified process evidence with **no media title
  anywhere in the payload**; and `move_audio_stream` is published as `unavailable` with its reason.
  Spotify and YouTube catalogue search both still return five selectable results on this runtime,
  and `/api/runtime` is unchanged.

  **The write path is deliberately not self-validated.** Setting a volume, muting, or switching
  output changes the machine the user is sitting at, so those steps are left for the user to run
  from the phone rather than performed automatically. Until they are, M2C must not be described as
  fully validated.

  **Not in this milestone:** per-application playback volume, card profile switching, Bluetooth
  pairing, and any provider's own player volume.

### M2D — Spotify playback with user OAuth

- **M2D — play the track you picked, and control the player that is playing it.** Merged as
  PR #18, with M2D.1. M2B3A.1 could find the exact song and open Spotify; it
  could not press play. This adds a `cofferdam/workstation/spotifyplayer/` module over the official
  Spotify Web API — one-time user authorization, a versioned playback snapshot, opaque Connect
  device handles, nine typed actions, and ten routes — plus a **Spotify player** panel in the PWA
  and *Play now* / *Add to queue* on Spotify track result cards. Documented in
  [`docs/SPOTIFY_PLAYBACK.md`](docs/SPOTIFY_PLAYBACK.md).

  **Authorization Code with PKCE, completed on the workstation.** PKCE needs no client secret, so
  the catalogue-search secret already on this host never enters the authorization path. The
  redirect is the loopback URI `http://127.0.0.1:8888/callback` — permitted by Spotify's current
  rules, where `localhost` is not — and the temporary listener binds to `127.0.0.1` only, serves
  exactly one path, and stops on success, failure or timeout. `127.0.0.1` on a phone is the phone,
  so Cofferdam opens the page in Opera on the workstation and the PWA says so in as many words
  rather than leaving someone waiting for a tab that cannot arrive.

  **The refresh token is the only durable half.** `secrets/spotify_user_oauth.json`, `0600` in a
  `0700` directory, written through `mkstemp` + `fsync` + `os.replace` so it is never momentarily
  world-readable and never half-written. The access token stays in memory. A refresh response
  **without** a new refresh token keeps the one already held, which the current PKCE documentation
  says can happen and which the naive reading would turn into a disconnection at the next restart.

  **No action reports what it has not observed.** Every player write answers `204 No Content` —
  Spotify acknowledging the request, not the speaker changing — and the documentation warns that
  execution order is not guaranteed. So each action re-reads playback and compares; `requested` and
  `observed` are separate keys. Playing a chosen track verifies that the item now playing is the
  item that was asked for. Queueing reports `accepted_by_provider` and explicitly does not claim
  playback started.

  **A Spotify device id is not an identity** — the documentation says "persistent to some extent"
  and allows `null` — so the client holds only an opaque `spdev-…` handle, re-resolved against a
  freshly read device list before every targeted action, with no fallback to matching a device by
  name. **Spotify publishes no mute operation**, so mute is volume-to-zero under the name
  `muted_by_cofferdam`, and unmute refuses rather than inventing a level when none was recorded.

  **Play now takes a search id and a result id and nothing else.** The server rebuilds the
  `spotify:track:…` URI from the private `ProviderItem` in the existing search session, so there is
  no request field for a URI, a track id or a device id to validate. Track results only; albums,
  artists and playlists are contexts and keep *Open in Spotify*.

  **Playback is personal activity.** Audit records carry the operation and the outcome and nothing
  else — no track, artist, album, query, account or device id — the package makes no logging call,
  the callback listener silences the HTTP access log whose request line would carry the
  authorization code, and `web/spotify.js` makes no `console` call at all. The authenticated PWA
  shows the current track; nothing else does, and no listening history is kept.

  1,570 tests pass on both CI paths (1,303 before this branch), including seven mutation checks
  covering callback state validation, loopback-only binding, client-supplied URI rejection,
  stale-device rejection, playback observation verification, unknown-unmute restore rejection, and
  secret redaction. One real defect was found while writing them: a disconnected account produced an
  empty device list, so every action refused with "no active device" — true of the list and false
  about the world, and it would have sent someone to switch on a speaker when what they needed was
  to authorize an account. The connection is now checked before any device is resolved.

  **Partially validated on the real host** (2026-08-05, from the phone against a real Premium
  account). Authorization completed in Opera on the workstation; the PWA connected without showing a
  token; catalogue search, queue, next, previous and switching between tracks all worked. **Three
  reliability defects were found**, and M2D.1 below fixes them. The end-to-end run has not been
  repeated since, so M2D is **not** yet fully validated.

### M2D.1 — cold start, eventual consistency, and response ordering

- **Fixing what the phone found.** Same branch, same PR. Three failures, one habit: looking once and
  believing it.

  **Play now with Spotify closed now opens Spotify.** One launch through the existing allowlisted
  application launcher — no shell, no constructed command line, no web page as a substitute — then a
  bounded wait for the Connect device, then the requested track. Spotify open-but-idle used to be
  refused identically; that device is now activated first with the documented transfer operation.
  Recovery is scoped to Play now: launching Spotify because somebody queued a track would be a
  surprise, so queueing is unchanged.

  **Every observation is now bounded confirmation rather than one read.** Spotify's player endpoints
  are eventually consistent, and the immediate re-read was reporting successes as failures — "set to
  80% but the device reports 50%" while the speaker was already at 80. Fixed attempt counts, fixed
  intervals, and a truthful give-up; playback is re-read, never re-sent.

  **The PWA drops stale responses.** A poll issued before a write could land after it and repaint the
  old value, which is why 50 → 80 showed 50. Every state-producing request carries a monotonic
  generation, in-flight reads are aborted when a write starts, and periodic polling pauses until the
  write is confirmed. A cheap `GET /api/spotify/activity` — no provider call — carries the phase the
  panel shows meanwhile.

  **Where several devices are eligible and none is active, Cofferdam asks.** Picking the first of
  three speakers would start music in a room nobody named.

  1,641 tests pass on both CI paths (1,570 before this fix), including three new mutation checks —
  removal of stale-response protection, reporting a requested volume instead of an observed one, and
  unbounded device polling. Writing them found one further defect and fixed it: the confirmation loop
  and the stale-mute-record cleanup collided, because a lagging read of the old non-zero volume looks
  exactly like the user turning it back up in the Spotify app, so the level to restore was dropped
  mid-mute and the following unmute refused.

  **Not re-validated on the real host yet.** The `90-spotify-playback-validation` drop-in is still not
  applied, and the live service still runs the M2C build under the unchanged `80` drop-in.

  **Not in this milestone:** seek, context playback, reading the Spotify queue, persisting a device
  preference, the YouTube dedicated player, and the Opera Companion.

### M2E — YouTube dedicated player

- **M2E — one player window, not a tab per video.** Merged as PR #19. M2B3A.1 could find the exact video and open it;
  every selection opened *another* Opera watch tab, Cofferdam could control none of them, and a
  tab appearing was treated as success. This adds a `cofferdam/workstation/youtubeplayer/` module
  over the official IFrame Player API — a Cofferdam-served player document, a bounded loopback
  control channel, a versioned player snapshot, ten typed actions and thirteen routes — plus a
  **YouTube player** panel in the PWA and *Play now* / *Add to queue* on video result cards.
  Documented in [`docs/YOUTUBE_PLAYER.md`](docs/YOUTUBE_PLAYER.md).

  **A player is a document that is currently saying so.** Connection state comes from a two-second
  heartbeat on the control channel, never from Opera's process list, so a running browser is never
  reported as a connected player and closing the tab is detected by the same mechanism that
  establishes it. One launch per bounded recovery, behind a lock, so two Play now presses arriving
  together cannot each open a tab; a launch that produces no player is a truthful timeout, never a
  second launch.

  **The queue is Cofferdam's, and that is a finding rather than a preference.** The IFrame API's
  `nextVideo()`, `previousVideo()` and `playVideoAt()` are documented *only* in terms of a YouTube
  playlist, with no defined behaviour when none is loaded. Passing an array of video ids would work
  and would hand YouTube the ordering and the advance-on-end behaviour — so Next is a Cofferdam
  decision implemented with `loadVideoById`, bounded at 25 items, in memory, and it refuses rather
  than playing a recommendation when nothing is queued.

  **The player page carries no token.** It is served by a second loopback-only listener bound to a
  module constant, defended by a Host-header check against DNS rebinding and by an
  `application/json` rule that forces a preflight no CORS header ever answers. The trust boundary
  is documented honestly: a same-user process could already read the token file, so the channel
  grants nothing new — and its vocabulary is closed in both directions, five commands out and three
  messages in, so a player page cannot request any Cofferdam action.

  **Autoplay is reported, not worked around.** The documented `onAutoplayBlocked` event becomes its
  own state with the chosen video left loaded and cued; one click on the player window enables the
  rest of the session, because media autoplay needs *sticky* activation, which is never consumed.
  Cofferdam sends `playVideo` once — no retry loop — and never starts muted and calls that success.

  **Nothing is claimed until the player reports it.** A volume set to 80 that the player still
  reports as 50 is a refusal carrying the observed value; a player showing a different video than
  the one chosen is `youtube_video_not_observed`, never success. Volume is refused rather than
  clamped outside 0–100. Mute uses the API's real `mute()`/`unMute()`, so unlike Spotify the field
  is plainly `muted`.

  **Three volumes, three panels.** YouTube's player level, Spotify's device level and this
  computer's speaker are independent; the player package imports no audio module at all, so that
  separation is structural rather than a rule to remember.

  1,819 tests pass on both CI paths (1,641 before), including seven mutation checks — duplicate
  launch prevention, client-supplied video-id rejection, observed-video verification, the queue
  bound, the PWA's stale-response guard, loopback-only binding and the Host check, and arbitrary
  player-command rejection.

  **Not re-validated on the real host yet.** The `95-youtube-player-validation` drop-in is not
  applied, and the live service still runs the M2D build under the unchanged `90` drop-in.

  **Not in this milestone:** seek, automatic queue continuation when a video ends, queue
  persistence, and the Opera Companion for Netflix/Prime Video/TV+.

**M2B does not change the M1 reboot gate.** It alters no boot behaviour, no systemd unit, and no
bind logic. Neither does M2B3A, M2B3A.1, M2C, M2D, M2E, M2F, or M2G. **M2H does**, which is why
the gate closes there.

## In progress (on a branch, not merged)

### M2H PR3 — Project Workstation Remote Control card and secure native-link open flow

On `feat/m2h-project-workstation-remote-control`. **M2H is still not complete and this PR does not
close the M1 reboot gate.**

PR1 (#23), PR2 (#24) and PR2.5 (#25) are merged. Together they gave Cofferdam a PTY-backed,
project-scoped, generation-aware Remote Control supervisor behind authenticated routes, with a
live-confirmed link format and a fail-closed capture gate. None of it had a user surface.

**What exists after this PR:** a Remote Control card in the Project Workstation PWA
(`web/remote.js`), rendering the capability flag, the six lifecycle states, evidence-backed
`awaiting_consent`, link availability *as a boolean*, and a bounded safe error summary. Start,
Stop and an explicit **Open Remote Control** control, state-aware and single-submission. Status
polling only — the link route is never polled, prefetched, or called to decide whether a button is
enabled. The capability URL is fetched inside one click gesture, validated against the backend
contract, used to navigate one opener-severed tab, and dropped; it reaches no markup, no `href`,
no browser storage, no log and no audit record. The link response now carries `Cache-Control:
no-store`, `Pragma: no-cache` and `Referrer-Policy: no-referrer`, and the page sets
`referrer: no-referrer`.

**The security boundary the card states, because it is real:** stopping the local host removes the
link from Cofferdam, and **does not revoke an Anthropic environment link already shared
elsewhere.** The native URL is environment-scoped, not launch-scoped — two generations produced
the same URL, and the CLI preserves the environment across restarts. Cofferdam has no
account-level revocation mechanism and the UI does not claim one.

**What is NOT in this PR, and must not be read as working:**

- **Transcript reading and prompt injection remain out of scope**, permanently under
  D-2026-08-08-3. The card supervises a session; it never looks inside one.
- **Unattended reboot recovery and linger are still unvalidated.** The unit template still ships
  with no `[Install]` section and is not enabled.

Next: **M2H PR4 — unattended reboot/linger recovery, cold-start phone reachability and M2H
milestone closeout.**

## Planned (active roadmap — see [`ROADMAP.md`](ROADMAP.md))

Queued, in order:

- **M2H — supervised Claude Remote Control** (Lane A): per-project native hosts as systemd user
  services, truthful health and authentication-expiry states, a native link in the PWA, reconnect
  and restart behaviour, and the unattended-reboot validation that closes the M1 gate above.
- **M2I — Claude Agent SDK adapter** (Lane B): structured `AskUserQuestion`, clarification
  questions kept apart from tool approvals, question and answer provenance, cancellation and
  restart parity, durable results and the `get_result` foundation. The merged CLI adapter is
  retired only after verified parity.
- **M2I.5 — private Custom GPT Actions bridge:** a dedicated narrow process, scoped per-client
  credentials, a production transport decision, the ten bounded Actions, and real iPhone
  validation against Cofferdam. No approval Action; no exposure of the general API or the PWA.
- **M2J — Project Workstation, workspaces and profiles:** workspace creation, project templates,
  a code-owned model allowlist, Auto / Safe / Review profiles, project-context retrieval, and
  handoff and history surfaces.

Later, unordered: Codex app-server as a second delegated worker and reviewer · Guardian/Supervisor
and Runtime A/B slots with the manual recovery command surface · update records and the A/B
self-update demonstration · process, window and display control · natural-language intent routing
(Ollama) · richer Markdown memory retrieval · an optional OpenClaw client.

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
- **An Xorg (X11) session as the MVP display baseline.** The product was built and validated on
  GNOME Wayland instead; see the marked baseline entry in [`ROADMAP.md`](ROADMAP.md).
- **M4's single `ClaudeCodeAdapter` owning both an interactive session and a delegated task.**
  Delivered early as M2F + M2G, and replaced by the two-lane architecture in D-2026-08-08-3.
- **The pre-M4 OpenClaw evaluation spike.** Nothing waits on it any more (D-2026-08-08-5).
