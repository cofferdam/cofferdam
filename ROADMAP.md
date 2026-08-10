# Roadmap — personal AI workstation

Seven milestones, ordered to put a visible product on a phone screen as early as possible, then
remote Claude, then the A/B self-update demonstration, then natural-language routing. Review
depth follows the post-pivot policy in [`DECISIONS.md`](DECISIONS.md) D-2026-08-01-6. Items
marked **OPEN QUESTION** are unresolved; each names the experiment that settles it.

**Read [Active implementation order](#active-implementation-order-recorded-2026-08-08) first.**
The M1–M7 sections remain the reference for what each layer *is*, and the M2x sections are the
record of what shipped; the work actually queued next is M2H → M2I → M2I.5 → M2J, and that
section takes precedence wherever the two disagree.

## Implementation philosophy (binding)

- **No council for ordinary work.** UI, adapters, media, and all M1 work need **tests plus
  self-review** — nothing more. Do not manufacture review ceremony, and do not add a gate merely
  because a question exists.
- **One focused review** is appropriate for: Guardian, activation, rollback, authentication,
  secret handling, privileged actions, and data migrations. One — not a repeating gate.
- **Cofferdam owns its core:** the UI, action schemas, task records, update records, the
  Guardian protocol, and A/B state. These are Cofferdam's canonical models.
- **OpenClaw is optional and replaceable** and must never become Cofferdam's canonical internal
  model. Nothing in the Guardian or activation path may depend on it.
- **Ollama may classify natural-language intent** into typed actions (M7). It may never execute
  arbitrary shell commands, and its output is always schema-validated before anything runs.
- **Trust Core is preserved but off the immediate critical path** — see
  [`DECISIONS.md`](DECISIONS.md) D-2026-08-01-7. Do not build on it now; do not delete it.
- **Ship the smallest thing that a phone can actually do**, then improve it with itself.

Baseline technical choices (Fable recommendations R-2, adopted here as working defaults until
contradicted by experiment):

- **Language/backend:** Python 3.12 + FastAPI + uvicorn. The repo is already Python; the Trust
  Core stays importable; Claude Code process control, Playwright, and Ollama all have
  first-class Python APIs. TypeScript adds a second toolchain for no MVP gain.
- **UI:** a static PWA (plain HTML/JS or a no-build lightweight framework) served by the
  runtime itself from `web/`. No separate frontend package, no bundler, until the UI outgrows it.
- **Network/auth (personal-use minimum):** the host joins a Tailscale tailnet; Cofferdam binds
  to the tailnet interface only (never 0.0.0.0 on a public interface). On top of that, one
  static device token entered once per client and sent as a header/cookie. HTTPS via Tailscale
  serve or plain HTTP inside the tailnet. No accounts, no OAuth, single user.
- **Live events:** WebSocket (bidirectional: task follow-ups, stop/continue need client→server;
  SSE would force a second channel). One `/ws` multiplexing typed event streams.
- **systemd:** user-level units in the logged-in graphical session (desktop control needs the
  session bus and display): `cofferdam-guardian.service` plus `cofferdam-runtime@a.service` /
  `cofferdam-runtime@b.service`, with lingering enabled so they survive logout/reboot.
- **A/B switching:** port-based. Guardian on a stable port (e.g. 7100), slot A on 7101, slot B
  on 7102. Guardian is the **discovery point, not a full proxy**, in the MVP: the PWA loads from
  Guardian's stable origin, asks `GET /active`, and connects directly to the active slot's
  port; on activation Guardian flips `/active` and the PWA reconnects. This avoids proxying
  WebSockets through Guardian and keeps Guardian tiny. **OPEN QUESTION:** whether PWA
  service-worker/origin quirks make the two-origin dance annoying enough to justify a thin
  reverse proxy (Caddy, or a ~100-line Python proxy) — settle during M5 by trying discovery
  first.
- **Reverse proxy:** none initially (see above).
- **Candidate strategy:** git worktrees per slot (`slots/a`, `slots/b` are worktrees of the
  same repo on different branches/commits), not packaged builds. Zero packaging work, exact
  provenance per slot, cheap diffing. Packaged builds reconsidered only if worktrees prove
  fragile.
- **Display/session:** ~~run the desktop as an **Xorg (X11) session**, not Wayland, for the MVP.~~
  **SUPERSEDED by what was actually built (2026-08-05, recorded here 2026-08-08).** The host runs
  **GNOME Wayland**, and M1 through M2G were designed, validated and merged on it: displays come
  from `org.gnome.Mutter.DisplayConfig` joined to `/sys/class/drm` rather than from `xrandr`
  (D-2026-08-05-2), screen capture reports `false` truthfully instead of being worked around, and
  window enumeration is published as `unavailable` with a reason (D-2026-08-05-3). No X11 session
  is required, and no milestone plans one. What remains open is not "when do we move to Wayland"
  but the two capabilities Wayland withholds: a portal-based capture path, and window control
  through a companion extension the user installs knowingly.
- **Browser automation:** Playwright (Python) driving **system Chrome/Chromium with persistent
  user-data dirs** under `~/cofferdam/profiles/`. Netflix DRM (Widevine) requires a real branded
  browser build — use Playwright's `channel="chrome"` with a persistent context. One-time
  manual login by Efe on the desktop persists in the profile; no plaintext passwords in config,
  no passwords through any model.

---

## Active implementation order (recorded 2026-08-08)

Where the milestone sections below are the design reference, this is the queue. M2F (Task Core,
PR #20) and M2G (the Claude Code CLI adapter, PR #21) are merged on `main`; the delegated-task
lane exists and has been driven from a phone. Client architecture and authority for everything
below are fixed by [`DECISIONS.md`](DECISIONS.md) D-2026-08-08-1 … -6.

### M2H — Supervised Claude Remote Control (Lane A)

- **Objective:** per-project native Claude Remote Control hosts, supervised by Cofferdam as
  systemd **user** services, under the session-lifecycle boundary in [`DESIGN.md`](DESIGN.md).
- **Visible result:** the PWA lists a project's Remote Control host with a truthful state, a
  native link that opens the real interactive session, and an honest failure or
  authentication-expired state instead of a spinner.
- **In scope:** supervision, health, authentication-expiry reporting, reconnect and restart
  behaviour, unattended-reboot validation.
- **Out of scope, permanently:** transcript scraping, mirroring session content, and injecting a
  prompt into a native session (D-2026-08-08-3).
- **Release gate:** **the open M1 post-reboot gate is closed inside this milestone.** M2H is the
  first work since M1 that changes what runs at boot, so the reboot section of
  `docs/checklists/m1-ubuntu-validation.md` is a required step here rather than a deferred one.
  See [`STATUS.md`](STATUS.md).
- **Review depth:** normal backend; the systemd unit template is the part worth one focused look.

### M2I — Claude Agent SDK adapter (Lane B)

- **Objective:** replace the CLI adapter's transport with the official Agent SDK, and gain the
  structured question channel the CLI could not give.
- **In scope:** structured `AskUserQuestion` support; **clarification questions kept separate from
  tool approvals**, because they are different acts and a client allowed to answer the first must
  not thereby answer the second; question and answer provenance; meaningful task activity;
  cancellation and restart parity with M2G; durable task results and the `get_result` foundation.
- **Retirement rule:** the raw Claude CLI adapter is removed only after the SDK adapter is
  verified against the behaviours PR #21 validated live — initial task, same-session follow-up,
  turn completion, polling, draft preservation, duplicate suppression, finish and slot release,
  cancellation isolation, repeated-cancel conflict, restart → `interrupted`, orphan cleanup,
  unrelated-session isolation, broad-log privacy.
- **Review depth:** normal backend.

### M2I.5 — Private Custom GPT Actions bridge

- **Objective:** the bounded Actions surface from D-2026-08-08-1, as a **dedicated narrow bridge
  process** in front of the task API — not a new route on the workstation daemon.
- **In scope:** scoped per-client credentials; the production transport decision (the probe's
  temporary tunnel is not one); the bounded Action set; real iPhone end-to-end validation against
  Cofferdam rather than against an echo service.
- **PR1 — shipped (local only).** A dedicated `cofferdam.actions_bridge` process, a scoped
  internal credential, a separate external Bearer key, eight Actions, the OpenAPI schema and the
  Custom GPT operator instructions. Loopback only; no tunnel, no DNS, no configured GPT.
- **The Action set as built, and why it differs from D-2026-08-08-1.** That decision recorded ten
  names so the first implementation could not quietly widen the surface. PR1 ships **eight**, and
  the difference is narrowing in one place and two small additions elsewhere.

  Four of the recorded reads — `get_task`, `get_updates`, `get_pending_questions` and `get_result`
  — are one Action, `sync_task`, returning one bounded snapshot. Four Actions where a model must
  make four calls to answer "what happened" is four chances to make three of them and guess the
  fourth; the consolidation removes the guess and publishes strictly less than the four separately
  would.

  `get_project_context` is **not** in PR1. It is M2J's — the roadmap already places project-context
  retrieval there — and it cannot be built honestly before the workspace model it reads from.

  Two Actions are new: `list_recent_tasks`, so a conversation that has lost its task reference
  recovers it rather than a model guessing an id; and `finish_task`, which is an existing Task Core
  lifecycle operation and the honest alternative to recording finished work as cancelled.

  Net: the surface is smaller than recorded, and every addition is a bounded read or an existing
  lifecycle verb. Recorded as D-2026-08-09-1.
- **PR2 — Gate A, shipped and validated.** A dedicated HTTPS origin on its own hostname, one
  Cloudflare Tunnel whose ingress names that hostname and a loopback port and answers 404 to
  everything else, the external key entered in the GPT editor, the schema imported, and a real
  private Custom GPT driving the bridge. The no-provider Preview passed; one approved Claude Code
  task then ran end to end in the disposable sandbox — one provider turn, an idempotent replay, a
  conflicting reuse refused, and a truthful finish.

  Validated on the **web GPT editor's Preview** and then, for the two read Actions, in the **native
  iPhone ChatGPT app** — which the original plan asked for. Mobile executed `listProjects` and
  `listRecentTasks` against the real origin and rendered the results. No write Action was called
  from the phone, so the consequential-confirmation prompt is still unverified there; that is the
  part most likely to differ between clients, and it is not claimed.
- **PR3 — Gate B, shipped and validated.** Production Agent SDK enablement, and with it the
  structured `AskUserQuestion` round trip the Claude Code adapter cannot demonstrate. `agent-sdk`
  installed in the candidate slot, the adapter registered by one removable drop-in, and a new
  disposable `agent-sdk-sandbox` project delegating to it — with `claude-sandbox` left on Claude
  Code, unchanged, as the regression baseline.

  **Registry ordering is no longer authority, and this had to land first.** The bridge used to take
  the first adapter a project listed, which is a real choice the moment two adapters coexist — and
  the list is sorted at load, so "first" meant *alphabetically first*. `delegated_adapter` makes it
  an explicit host decision; ambiguity fails closed; a single permitted adapter still resolves
  implicitly so no existing registry needed rewriting. No OpenAPI change and no Custom GPT edit.

  One live task validated the whole workflow from the **native iPhone app**: a real single-choice
  clarification, the displayed choice mapped to the Cofferdam-minted `option_id`, provenance
  `future_gpt_bridge`, the same provider session across the continuation and one follow-up, a
  truthful finish, and a byte-identical sandbox. All four consequential Actions prompted rather than
  mutating silently — closing the mobile gap PR2 left open. Evidence in
  [`docs/checklists/m2i5-gate-b-validation.md`](docs/checklists/m2i5-gate-b-validation.md).

  **Only the single-choice shape is supported.** Free text, multiple choice, several simultaneous
  questions and "Other + custom text" stay unsupported and return a bounded unsupported result; one
  live question establishes nothing about them, and nothing was widened to fit. Tool approvals are
  still never bridged.
- **Out of scope:** any approval endpoint, and any exposure of the general Cofferdam API or the
  PWA through the bridge's transport (D-2026-08-08-2).
- **Prior art:** the 2026-08-08 capability probe, recorded in [`STATUS.md`](STATUS.md). It proves
  the client path works from a real phone; it proves nothing about this bridge, which does not
  exist yet.
- **Review depth:** high-risk — this is an internet-reachable transport and a credential
  boundary. One focused architecture review before implementation, one after.

### M2J — Project Workstation, workspaces and profiles

- **Objective:** the surface a person actually works from, and the project context an Action can
  retrieve.
- **In scope:** existing / new / temporary workspace creation; project templates; a **code-owned**
  model allowlist; Auto / Safe / Review profiles; the Project Workstation interface; project-context
  retrieval for the Custom GPT (`get_project_context`); handoff and history surfaces.
- **Review depth:** normal backend; the profile semantics deserve one focused review, because a
  profile that quietly widens what a task may do is the failure worth designing against.

### Later, unordered

Codex app-server as a second delegated worker and reviewer in Lane B · richer Markdown memory
retrieval under D-2026-08-08-6 · an optional OpenClaw client under D-2026-08-08-5 · an MCP or App
transport, only when it materially improves the Actions path that has been proven to work · a
local personal assistant · voice, STT and TTS.

---

## M1 — Remote control skeleton: the first real product

**M1 is not a metrics dashboard.** It is the first genuinely useful control surface: the phone
sees the host *and can make the host do things*.

- **Objective:** Cofferdam runs continuously on the Ubuntu host, survives reboot, and a phone
  can securely connect, watch live host status, take a screenshot, launch a browser, and open a
  URL on the host.
- **Success condition:** *From a phone, Efe can connect to Cofferdam, see live host status,
  request a screenshot, and open a URL on the Ubuntu host.*
- **Visible result:** open Cofferdam from phone/tablet on the tailnet → paste the device token
  once → live status cards (host up, CPU/mem/disk, session type, Cofferdam version, uptime) →
  tap **Screenshot** and see the host's screen → tap **Open Firefox** → type a URL, tap **Open**
  and watch it appear on the host — and all of it still works after an unattended host reboot.
- **Minimum components:**
  - FastAPI app + uvicorn; token auth middleware; structured error envelope.
  - Endpoints: `/healthz`, `/api/status`, `/api/actions` (typed dispatch), plus the three
    convenience routes `/api/actions/screenshot`, `/api/actions/open-application`,
    `/api/actions/open-url`; `/api/screenshots/{id}` (authenticated retrieval).
  - Typed action registry + schemas (`take_screenshot`, `open_application`, `open_url`) with
    action IDs, timestamps, and status — no free-form command field anywhere.
  - Host adapter interface + platform implementations (Linux/X11 first, Windows dev
    implementation, and an explicit stub for unsupported hosts).
  - WebSocket event channel (`/ws`) with heartbeat, action-state broadcast, reconnect.
  - Responsive PWA (phone + tablet layouts): connection status, token setup, status cards,
    screenshot button/viewer, application launcher, URL field, result toasts, recent actions.
  - Bounded JSON persistence: config, recent action records, last known status.
  - systemd unit + `loginctl enable-linger`, Tailscale-bound listener, `docs/host-setup.md`.
- **Implementation notes:** Ubuntu prep is part of this milestone (runbook: install Ubuntu
  Desktop, auto-login to an Xorg session, disable sleep/suspend, install Tailscale, enable
  lingering, clone, venv, enable unit). Every UI control issues a **typed action** through the
  same executor path that Ollama will later feed (M7) — no side channels, no shell passthrough.
  Screenshots are returned through authenticated API responses, never written under the static
  web root. Guardian does not exist yet: the runtime runs standalone on its final slot-A port so
  M5 can slide Guardian in front without reworking the UI.
- **Acceptance tests:** automated — `/healthz`; auth required; invalid token rejected; status
  schema; unknown action rejected; no shell command can be submitted; `open_url` scheme
  validation; action results carry ID/timestamp/status; event clients receive action-state
  updates; screenshots require authentication; adapter failures surface as bounded structured
  errors; no committed secrets in config. Host validation — the Ubuntu checklist in
  `docs/checklists/m1-ubuntu-validation.md`, run **without stubs**, ending in a reboot test.
- **OPEN RELEASE GATE — post-reboot automatic startup is not validated.** The Ubuntu host
  validation above passed on 2026-08-03 (Ubuntu 26.04, GNOME/Wayland) **within a single
  continuously logged-in session**. The reboot test that ends the checklist has **not** been run:
  **Closed 2026-08-09 inside M2H PR4.** A real cold reboot was performed and the phone was used
  before any desktop login: the user manager started through lingering at boot, `tailscaled` was
  not ready in time and the daemon waited 6 seconds for its own address instead of dying in the
  old restart loop, the listener re-bound to the Tailscale address unattended, and the phone
  reached Cofferdam 50 seconds after power-on — 6h48m before the graphical session started. The
  saved device token survived. The one item **not** re-observed is graphical-session capability
  reporting before and after login, which remains covered by tests only. Full evidence table in
  [`STATUS.md`](STATUS.md).
- **Dependencies:** none.
- **Review depth:** low-risk — tests + self-review, no council. (The device-token middleware
  gets one focused look at M5, when activation control starts riding on it.)
- **Deferred to M2+:** second-display placement, process management/kill, window control,
  fullscreen, media/volume, YouTube search, Guardian and A/B, Wayland, HTTPS hardening beyond
  the tailnet.

## M2A — Control plane foundation: registries, IDs, aliases, browser profiles

- **Objective:** give the product a vocabulary. Before Cofferdam can move a window to "büyük
  monitör", open a URL in "kişisel tarayıcı", or hand a conversation to "cofferdam claude", those
  names have to exist somewhere validated, stable, and free of secrets. M2A is that layer and
  nothing more.
- **Visible result:** from the phone: read-only cards listing this machine's devices, named
  displays, applications, browser profiles, agent-profile placeholders, and conversation-route
  templates — with honest loading, empty, invalid and unavailable states — plus a browser-profile
  selector on Open URL that actually changes which browser opens the page.
- **Minimum components:**
  - Six versioned JSON registries under `$COFFERDAM_HOME/config/registries/`, never in Git;
    committed placeholders in `examples/registries/`.
  - Strict typed models, readers, cross-registry reference validation, normalized alias indexes,
    lookup by stable ID and by alias, an atomic writer utility, safe empty defaults, and bounded
    structured errors suitable for API responses.
  - `GET /api/registries` and `GET /api/registries/{registry_name}` — authenticated, **read-only**.
  - `open_url` gains an optional `browser_profile_id`; domain policy enforced before launch;
    bounded Opera detection; unchanged legacy behaviour for URL-only requests.
  - PWA: read-only registry sections, browser-profile selector, no fake Start/Send/Run/Route.
  - Architecture documents: `docs/CONTROL_PLANE.md`, `docs/DEVICE_REGISTRY.md`,
    `docs/APPLICATION_PROFILES.md`, `docs/AGENT_ROUTING.md`, `docs/DESKTOP_APP.md`.
- **Implementation notes:** registries are declarative — a registry selects among capabilities the
  code already has and can never introduce one. No executable path, argv, command string, shell
  fragment, desktop-file path, environment override, credential, cookie, or live tab/conversation
  ID is representable in any schema. Alias matching folds Unicode case plus a Turkish
  dotted/dotless-I tailoring; ambiguity is a validation failure, never a guess. Launching still
  goes through the M1 verified Wayland graphical-session launcher.
- **Acceptance tests:** empty/missing/malformed registries; unknown schema version; unknown and
  forbidden fields; duplicate IDs and duplicate normalized aliases; Turkish normalization;
  ambiguous alias rejection; cross-registry dangling references; invalid EDID hash; atomic
  persistence including a failed write preserving the original; browser-profile default
  uniqueness; disabled profiles; domain allow-all, allow-list exact host, subdomain, and
  `badexample.com` boundary rejection; bounded Opera candidate detection and unavailable result;
  explicit and invalid `browser_profile_id`; backward-compatible URL-only `open_url`;
  authenticated/unauthenticated/unknown registry endpoints and error redaction; agent profiles
  remaining placeholders; routes remaining templates; local registries staying untracked.
- **Dependencies:** M1 (code merged; its reboot gate is orthogonal and stays open).
- **Review depth:** low-risk — tests + self-review. (The domain-policy boundary is the one part
  worth a second look when profile editing arrives.)
- **Explicitly not in M2A:** Raspberry Pi control, Wake-on-LAN or physical power actions, window
  movement or display placement, browser DOM access, ChatGPT/Claude web automation, browser
  extensions, agent execution, Claude Code session execution, message sending, natural-language
  action planning, desktop application scaffolding, registry write APIs, and any reboot behaviour
  change.

## M1.1 — Service lifecycle correction (unplanned; forced by a regression)

- **Objective:** make enabling Cofferdam safe for the host's graphical login. The M1 unit left
  Ubuntu unable to log in at all.
- **Binding constraint (D-2026-08-04-1):** Cofferdam observes and follows the graphical session;
  it never creates, fakes, starts, stops, restarts, terminates, or owns it. A unit that can start
  before login must never name `graphical-session.target` in an activating directive — `Wants=`
  is an activation request, not a wait. Lingering is never evidence that a session exists.
- **Visible result:** the host boots, the API is reachable from the phone before anyone logs in
  with GUI capabilities reported `false`, graphical login succeeds normally, and capabilities
  become true only once a real session exists.
- **Acceptance:** `docs/checklists/m1-ubuntu-validation.md` steps L1–L10, including **two**
  reboots — one successful login is explicitly not sufficient.
- **Architecture note (D-2026-08-04-2):** a daemon/session-agent split was evaluated and
  deferred. Cofferdam delegates every application launch to the systemd user manager, which is
  what actually holds the session, so a single always-on unit never has to pretend it has one.
  Revisit if Cofferdam ever needs to hold live session-scoped resources itself.
- **Dependencies:** none. Blocks completion of M1's Ubuntu validation.

## M1.2 — Truthful screenshot capability (unplanned; forced by a finding)

- **Objective:** a reported capability must describe the live session, not this process. A daemon
  started at boot advertised `screenshot: true` on a Wayland host because `scrot` was on `PATH`.
- **Binding constraint (D-2026-08-05-1):** graphical state is read from the verified session, never
  from the daemon's own frozen environment; absence of a variable is not evidence of the opposite
  value; capabilities are recomputed per request and never cached across a logout.
- **Status:** merged and validated live. Screen capture under Wayland is still **not implemented**
  — `screenshot: false` is the truthful answer, not a placeholder for a bug.

### Deferred from M1.2 (non-blocking)

- **Wayland-compatible remote screen capture.** One-shot capture that actually works under
  Wayland, and later live remote viewing, plus **named-display selection** so a capture can target
  a chosen display rather than "the screen". Constraints already established on this host:
  `scrot`/`maim`/`import` return a black frame under Wayland;
  `org.gnome.Shell.Screenshot.Screenshot` returns `AccessDenied` to a non-portal caller; and
  `org.freedesktop.portal.Screenshot.Screenshot` never emits a `Response` even with
  `interactive: false`. `gnome-screenshot` is the adapter's preferred tool and remains
  uninstalled and unvalidated here. **The current truthful `screenshot: false` is acceptable until
  this is implemented**, and named-display selection depends on M2B display discovery.

## M2B — Runtime inventory: what is actually here right now

> **M2B1 is implemented** on `feat/m2b-runtime-inventory-foundation` (not merged): read-only
> discovery of displays, processes and application instances, an authenticated read-only
> `/api/runtime`, and a *Live system* panel. Windows are reported `unavailable` with a reason —
> no safe read-only backend exists on GNOME Wayland. Backends, evidence and limitations:
> [`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md). What remains is **M2B2**, below.

- **Objective:** the layer M2A deliberately does not have. M2A knows what the code can do
  (definitions) and what the user chose to call things (overlays); M2B discovers the **runtime
  resources** that actually exist.
- **Scope:** connected displays · running processes · application instances · windows. Later, and
  not in the first pass: browser tabs (through a browser companion) and agent task instances.
- **Binding constraint (D-2026-08-04-6):** discover the resource first, *then* attach a label.
  A registry entry is an optional overlay and never evidence that a resource exists.
- **Identity rules this milestone must implement** (recorded now so the implementation cannot
  quietly pick something weaker):
  - A **PID is visible and usable, but is never a stable identity on its own** — PIDs are reused,
    and a stale PID plus an action is how the wrong process gets terminated.
  - **Application instance identity** = host/device identity + boot identity + PID + process
    start time. Before controlling or terminating a process, Cofferdam re-verifies that the PID
    *and* start time still identify the same process.
  - **Never terminate an application on a broad command-name match.**
  - **Display identity prefers a hardware fingerprint** — EDID (or its hash) plus the owning
    device. Connector names such as `HDMI-1` or `eDP-1` are runtime *hints*, not identities.
  - **Browser tabs use browser-extension/browser-API tab and conversation IDs**, and are never
    inferred from Chromium process PIDs.
  - **User labels are overlays**, attachable at creation where creation is Cofferdam-owned, or
    after discovery, or later through the desktop or mobile UI. A label is never the identity.
- **Honest-empty rule:** a machine with no registry files stays fully working, and the UI shows
  empty/configuration states rather than sample data.
- **"Available" is not "running" — the UI must say which it means.** Observed during PR #9's live
  validation: the M2A card reads *Firefox available*, and that is true only in the definition
  sense — the application is installed and launchable. It says nothing about whether Firefox is
  running right now, and a reader can easily take it the other way. M2B must present these as
  four distinct things and never let one stand in for another:
  1. **application definition** — the concept exists and Cofferdam knows how to launch it;
  2. **available to launch** — a definition whose executable was found on this host;
  3. **running application instance** — an actual process, with verified PID **and** start time;
  4. **current windows** — belonging to an instance.
- **Display discovery behaviour** (approved; implement in this milestone):
  - Discover **all currently connected displays** live, including the laptop panel and every
    external display. Discovery is the source of truth; a registry entry never conjures one.
  - Show each display's **real system/hardware identity first** — connector, model, resolution,
    and an EDID-derived fingerprint where the system exposes them. Report only what was actually
    read; absent fields stay absent rather than being guessed.
  - Let the user **select a discovered display and add or edit a label/alias** for it, from the
    desktop or mobile UI. *(M2B2 — the one part of this section the foundation does not ship.)*
  - The label is an **optional overlay layered on top of** the hardware identity. It never
    replaces, renames, or becomes that identity, and removing it must leave the display fully
    identified.
- **Dependencies:** M2A (vocabulary and IDs). Blocks the useful parts of M2 and M3, and blocks
  named-display selection for Wayland capture.

### M2B2 — labels and aliases on discovered resources (immediate follow-up)

The one piece of M2B above that the foundation does **not** implement. M2B1 *resolves* overlays a
user already wrote by hand; M2B2 lets them be created and edited from the UI.

- **Flow:** the user selects a discovered card in the PWA or the desktop client → adds or edits a
  label and aliases → the overlay is written **atomically** (the existing `write_json_atomic`,
  which has tests and is still wired to no route) → the resource keeps its system identity, with
  the label layered on it.
- **Keyed to the stable identity**, never to a connector or a PID: the EDID fingerprint for a
  display. A display later disconnected keeps its overlay and stays distinguishable from a
  connected one, because the key describes the panel rather than the socket it was in.
- **No new identity model is needed.** Every discovered resource already carries a stable
  `resource_id` and an `overlay` slot, and the resolver already refuses ambiguous matches and
  connector-hint-only matches.
- **The first write path into the registries.** M2A and M2B1 are entirely read-only over the
  network, so this milestone owns the whole question of network-reachable configuration writes:
  validation before write, bounded payloads, what an overlay may and may not contain, and whether
  a write needs confirmation. That is why it is a separate milestone rather than a patch.
- Also in scope, if cheap: aliases for discovered **application instances**, using the same
  mechanism. Not in scope: anything that turns an overlay into a capability.

### M2B3A — media and application launch profiles (implemented)

> **Implemented** on `feat/m2b3a-media-launch-profiles` (not merged). Spotify, YouTube, Netflix,
> Prime Video and TV+ as a code-owned launch catalogue; Opera as Cofferdam's default browser;
> `open_media_provider` / `search_media_provider`; a Media section in the PWA. Documented in
> [`docs/MEDIA_PROFILES.md`](docs/MEDIA_PROFILES.md), decided in [`DECISIONS.md`](DECISIONS.md)
> D-2026-08-05-5 and -6.

It is a **launch** surface, not an integration: it opens applications and pages and claims nothing
about playback. The two adapter seams that would change that — a Spotify semantic adapter with
OAuth and real playback control, and a browser companion doing semantic DOM search on an
already-authenticated service tab — are specified in that document and deliberately not built.

### M2B3A.1 — official-provider search and result selection (implemented)

> **Implemented** on `feat/m2b3a1-media-result-selection` (not merged). Official Spotify Web API
> and YouTube Data API v3 catalogue search, up to five result cards, and opening the exact selected
> item. Documented in [`docs/MEDIA_RESULTS.md`](docs/MEDIA_RESULTS.md), decided in
> [`DECISIONS.md`](DECISIONS.md) D-2026-08-05-7 and -8.

The server stays the authority: search returns opaque handles, and a chosen result is re-resolved
server-side into a launch target the client never sees. Credentials are a local 0600 file, never a
PWA form. Still no playback claim, and Spotify playback control is unreachable by construction.

### M2C — audio control foundation (implemented)

> **Implemented** on `feat/audio-control-foundation` (not merged). Reading and safely changing the
> workstation's PipeWire/WirePlumber audio state from the phone: current output, connected outputs,
> system volume, mute, and active playback streams. Documented in
> [`docs/AUDIO_CONTROL.md`](docs/AUDIO_CONTROL.md).

The first routes in the product that change the *physical* state of the machine, so the surface is
the narrowest yet: a resource id, an integer, and a boolean. A PipeWire node id is never an
identity — it is reused after its object is destroyed — so an output is addressed by a digest over
host, audio graph and stable node name, and re-verified against a fresh graph read immediately
before acting.

Volume is read and written on one scale (`wpctl`'s perceptual scale, which is also GNOME's), never
the linear gain PipeWire stores, so the phone and the laptop screen agree. Every action re-reads
the host and reports observed state; an accepted command that did not take effect is reported as
`not_applied`. Moving an already-playing stream is published as **unavailable** with its reason
rather than implemented, because WirePlumber offers no command for it and the metadata workaround
would pin an application to an output for future sessions.

Streams are associated with an application only through the daemon's kernel-verified
`pipewire.sec.pid`, never a self-declared name; what is playing is never read.

**Not in this milestone:** per-application playback volume, card profile switching (turning an HDMI
output on), Bluetooth pairing, and any provider's own player volume.

### M2D — Spotify playback with user OAuth (implemented)

> **Implemented** on `feat/spotify-playback-oauth` (not merged). Controlling the user's *own*
> Spotify account: playback state, pause/resume, previous/next, Spotify's player volume, its
> Connect devices, and playing or queueing a track chosen from an existing verified search result.
> Documented in [`docs/SPOTIFY_PLAYBACK.md`](docs/SPOTIFY_PLAYBACK.md).

Authorization Code with **PKCE**, which needs no client secret — so the catalogue-search secret
already on the host never enters the authorization path. The redirect is the loopback URI
`http://127.0.0.1:8888/callback`, which Spotify's rules permit and which `localhost` would not
satisfy. A temporary listener binds to `127.0.0.1` and nothing else, serves one path, and stops on
success, failure or timeout. `127.0.0.1` on a phone is the phone, so the page opens in Opera **on
the workstation** and the PWA says so rather than leaving someone waiting for a tab that cannot
arrive.

The refresh token lives in `secrets/spotify_user_oauth.json`, `0600` in a `0700` directory, written
atomically; the access token is never persisted. A refresh response without a new refresh token
**keeps** the one already held — Spotify documents that this happens, and treating it as loss would
disconnect a working account at the next restart.

Every action re-reads playback and reports what it observed. A Spotify device id is documented as
persistent only "to some extent", so the client only ever holds an opaque handle, re-resolved
against a fresh device list before any targeted action. Spotify publishes no mute operation, so mute
is volume-to-zero under the name `muted_by_cofferdam`, and unmute **refuses rather than guessing**
when no restore level is known. Play now and Add to queue take a search id and a result id and
nothing else — the server rebuilds the track URI from the session it privately remembers, so there
is no request field for a URI to validate.

**Not in this milestone:** seek, context playback (albums, artists, playlists), reading the queue,
persisting a device preference, and any YouTube player.

### M2B3A.2 — Opera Companion foundation (next)

The seam that would bring Netflix, Prime Video and TV+ into structured results: an approved
companion identifying the already-signed-in service tab and performing a *semantic* search within
it, returning real result cards the user picks from. No coordinates, no OCR, no screenshots, no
blind first-result clicking. It is also where OQ-3 (TV+ search needing a storefront region) becomes
answerable.

### M2B3B — safe application close and restart

Closing and restarting an application *instance*, following the M2B identity rule: PID plus start
time, re-verified immediately before acting. Not in M2B3A, which terminates nothing and sends no
process signals.

### Beyond M2B3A.1, still not implemented

Browser tab inventory (needs a browser companion reporting the browser's own tab IDs; never
inferred from renderer processes) · agent task inventory · task resource audit · window discovery
on GNOME Wayland (needs a companion extension the user installs knowingly) · **any** process,
window, or display *control*.

## M2 — Desktop hands: processes, windows, displays

- **Objective:** the rest of semantic desktop control — beyond M1's launch/screenshot/URL.
- **Now builds on M2A:** the display registry supplies the stable IDs and human aliases that
  `move_window_to_display` and per-display screenshots target, so "büyük monitör" resolves to
  `large-monitor` rather than to a positional index that changes when a cable moves.
- **Visible result:** from the phone: see running applications and relevant processes; close an
  application; move a browser or media window to display 2; screenshot a chosen display.
- **Minimum components:** process adapter (`psutil` list + terminate); window adapter
  (`wmctrl`/`xdotool`: match by PID/class, move/resize, fullscreen); display registry from
  `xrandr` geometry; per-display screenshot targeting; extended action schemas
  (`close_application`, `move_window_to_display`, `set_fullscreen`).
- **Implementation notes:** same typed-action path as M1. Window placement = match window by
  PID/class, then `wmctrl -e` into the target display's geometry.
- **Acceptance tests:** unit tests for the new action schemas; on-host integration:
  `open_url(display=2)` → window geometry lies inside display 2's bounds; screenshot per display
  decodes; `close_application` terminates only the matched process.
- **Dependencies:** M1.
- **Review depth:** low-risk.
- **Deferred:** media/volume, browser profiles, any model involvement, Wayland.

## M3 — Media: YouTube, Netflix profile, fullscreen, volume

- **Objective:** the workstation as a phone-driven media station.
- **Visible result:** from the phone: search YouTube for a named video and play it on display 2
  fullscreen; adjust volume; open Netflix already logged in; open a Netflix title URL; when
  automatic selection is uncertain, the phone shows the candidate results to pick from.
- **Minimum components:** browser adapter (Playwright, persistent `profiles/media` context,
  Chrome channel); `search_and_open_media` action (provider: youtube|netflix, query/url,
  display, fullscreen); volume adapter (`pactl`); fullscreen via keyboard event (`f` on
  YouTube) or window-state; result-disambiguation card in the PWA.
- **Implementation notes:** progression: open URL → open on chosen display → YouTube search
  results (semantic selectors, top-N titles+thumbnails) → auto-play top result when confidence
  is high, else return choices. Netflix: one-time manual login on the desktop into the media
  profile; session persists in the profile dir; treat profile dir as sensitive (not in git,
  backed up separately). Prefer DOM/semantic APIs over coordinate clicks throughout.
- **Acceptance tests:** deterministic: profile-persistence test (cookie survives context
  restart), URL-on-display-2 geometry test, volume set/get roundtrip. YouTube search/play and
  Netflix are verified by a scripted manual checklist (`docs/checklists/m3-media.md`) — they
  exercise third-party UIs and are accepted as personal-use-reliable, not CI-guaranteed.
- **Dependencies:** M2.
- **Review depth:** low-risk.
- **Deferred:** voice, recommendations, any media library features.

## M4 — Remote Claude: tasks as cards

> **Largely delivered early, and partly superseded (2026-08-08).** M4's objective shipped ahead of
> M2 and M3 as **M2F** (provider-neutral Task Core, PR #20) and **M2G** (the Claude Code adapter,
> PR #21): a phone picks a project, sends a prompt, watches truthful state, follows up in the same
> session and cancels one task. What is superseded is the *shape* recorded below — a single
> `ClaudeCodeAdapter` owning both the interactive session and the delegated task. That is now two
> lanes (D-2026-08-08-3), and the CLI transport described here is replaced by the Agent SDK in
> M2I. The two OPEN QUESTIONS below were settled by the M2G probes against the installed CLI and
> by the host-owned project registry; the resource-audit and card-architecture subsections that
> follow are **not** superseded and remain the plan of record.

- **Objective:** start, observe, steer, and stop Claude Code tasks from the phone.
- **Visible result:** pick a project, type/paste a prompt, get a task card: state
  (running/waiting/done/failed), the **latest meaningful assistant output** by default,
  expandable full log; reply to a waiting task; stop a task; card survives phone
  disconnect/reconnect.
- **Minimum components:** `ClaudeCodeAdapter` (spawn `claude -p --output-format stream-json
  --input-format stream-json --verbose` per task in the chosen workspace; parse the event
  stream; maintain task state machine; full raw log to `logs/tasks/<id>.jsonl`); task model +
  SQLite persistence; task cards + detail view in the PWA; workspace registry (allowed project
  dirs, each task in a chosen or freshly created worktree).
- **Implementation notes:** "latest meaningful output" = the newest complete assistant text
  block from the stream-json events, with tool-use noise filtered; "waiting for input" =
  process alive with a pending user-turn (stream-json `result`/turn events make this
  detectable). Follow-ups are written to the process's stdin as stream-json user messages.
  Tasks are host-side processes owned by the runtime, so UI reconnects re-attach by task ID.
  **OPEN QUESTION:** exact waiting-state detection fidelity across Claude Code versions —
  settle with a 1-day spike against the installed CLI before building the state machine.
  **OPEN QUESTION (multiple Claude accounts):** separate Linux users vs separate
  `HOME`/`CLAUDE_CONFIG_DIR` per profile. Recommendation: separate Linux users if ever needed
  (cleanest supported isolation); not an MVP blocker; no account rotation to evade provider
  limits.
- **Acceptance tests:** adapter tests against a fake stream-json emitter (state transitions,
  meaningful-output extraction, reconnect); one live smoke test with a trivial prompt in a
  scratch workspace; kill-and-reattach test.
- **Dependencies:** M1 (M2/M3 not required).
- **Review depth:** normal backend — tests + one balanced review of the adapter/state machine
  if it turns out subtle.
- **Deferred:** multi-agent orchestration, ChatGPT automation (the media profile from M3
  already allows "open ChatGPT logged in"; pasting externally prepared prompts already works).

### Required later: per-task resource audit (recorded now, not implemented)

Every task card should expose an expandable **resource audit** — what the task actually touched,
where evidence exists for it:

files read · files modified · files created · files deleted · applications accessed · browser
profiles or tabs accessed · URLs and domains opened · processes with **verified** PIDs and start
times · external connectors used · approvals granted · artifacts, commits, and outputs produced.

Each resource event correlates to: task ID · originating request · actor/agent · operation type ·
timestamp · resource identity · **evidence source** · result.

Evidence source is one of: an agent-reported tool event · a Git diff · an OS-observed process
event · a browser-extension event · a Cofferdam-owned action execution.

**The honesty rule that makes this worth having:** Cofferdam must not claim complete visibility
into file reads or resource access when the adapter or the operating system did not provide that
evidence. An unobserved read is reported as unknown, never as "did not happen". Process entries
follow the M2B identity rule — PID plus start time, verified.

### Card and UI architecture (recorded now; no visual redesign in this milestone)

- Desktop and mobile consume the **same backend resource/task model**. The model is the contract;
  the clients are views of it.
- Cards may have **compact and expanded** forms; mobile cards may expand on selection.
- Cards may later be connected in a Lego-like or graph-like representation, where **visual edges
  represent real task/routing relationships** — never decoration.
- **UI position must never become business logic.** Where a card sits on screen carries no
  meaning the backend does not already hold.
- The final design language is decided separately. Until then, **truthful functionality outranks
  visual polish**.

## M5 — Guardian and A/B slots

- **Objective:** the supervised two-slot layout with health checks, activation, and rollback —
  operated manually first.
- **Visible result:** the dashboard shows Guardian status, active slot, candidate slot, and
  slot versions; from the phone: start the candidate, watch health checks pass, activate it,
  see the UI reconnect to the new version, roll back.
- **Minimum components:** `guardian/` (its own small package: slot registry, process control of
  the runtime units, `/healthz` probing, `/active` discovery endpoint, activation + automatic
  rollback on failed post-switch checks, append-only `updates/` and log custody); runtime
  `/healthz` (self-checks: DB reachable, adapters import, WS alive); slot worktree layout;
  Guardian protocol (small JSON over localhost, allowlisted verbs); PWA reconnect-on-switch;
  **the manual recovery command surface** (below).
- **Manual recovery contract (required, not optional):** every A/B operation must be doable
  from a plain shell — locally or over Tailscale SSH — with **no AI, no runtime UI, and no
  Guardian process required**. Ship these small scripts in `guardian/bin/` (on `PATH` via the
  host-setup runbook):

  | Command | Does |
  |---|---|
  | `cofferdam-status` | prints active slot, slot versions/commits, per-slot service state, Guardian state, last activation and rollback — reading files only, works with everything else down |
  | `cofferdam-pin-slot a` / `... b` | forces the active slot to A or B and pins it, so Guardian (or an update) cannot switch it back until unpinned |
  | `cofferdam-rollback` | switches back to the previously active slot and pins it; pure file + systemd operations, no AI, no network |
  | `cofferdam-start` / `cofferdam-stop` | start/stop the runtime for a named slot (default: the active one) directly through systemd, bypassing Guardian |

  Requirements this contract encodes: **active-slot state is a plain inspectable file** —
  `~/cofferdam/state/active-slot.json` (`{slot, pinned, previous_slot, changed_at, reason}`),
  human-readable and hand-editable in an emergency; Guardian *reads* it and is not its only
  writer. A dead, broken, or removed Guardian must never make the previous runtime impossible to
  start: the slot services are ordinary systemd units that run standalone. Recovery steps
  (including "Guardian is down and the phone shows nothing" and "both slots fail to start") are
  documented in `docs/recovery.md`, which must be readable and followable without Cofferdam
  running.
- **Implementation notes:** Guardian is deliberately dumb: fixed command set, no model calls,
  no dynamic code. Post-activation monitoring: Guardian polls `/healthz` and WS liveness for a
  soak window (e.g. 10 min); threshold failures → automatic rollback to the previous slot,
  which is never modified during the soak. Candidate isolation in MVP: workers get the
  candidate worktree as cwd; before activation Guardian runs a deterministic tamper check —
  `git status`/diff of the *active* slot and a hash manifest of `guardian/` must both be clean;
  any unexpected change aborts activation. (True OS-level enforcement — separate users/ACLs —
  is a later hardening step and the future Trust Core insertion point.)
  **OPEN QUESTION:** discovery-endpoint vs thin proxy (see baseline choices) — decide here.
- **Acceptance tests:** Guardian unit tests (state machine, rollback triggers, tamper-check
  abort); end-to-end on host: kill active runtime → Guardian restarts it; activate broken
  candidate (failing `/healthz`) → activation refused; break it *after* activation → automatic
  rollback within the soak window.
- **Dependencies:** M1 (M2–M4 keep working unchanged inside the slots).
- **Review depth:** high-risk — targeted experiment (two-slot switch prototype) first, one
  focused architecture review, implementation, one focused implementation review.
- **Deferred:** Guardian self-update path (manual via SSH only), Trust Core wiring, low-risk
  auto-activation policies.

## M6 — Self-update: the clock-card demonstration

- **Objective:** the full update-record loop, end-to-end, on the smallest real feature.
- **The first demonstration is deliberately constrained.** It must be a **stateless UI change**
  — the reference case is *"Add a system clock card to the dashboard."* The first demo must
  **not** include: database schema migration, persistent data migration, any Guardian
  modification, secret-format changes, package-manager/dependency changes (unless genuinely
  unavoidable, and then called out and reviewed), or destructive filesystem operations. Those
  categories only become eligible after the plain stateless loop has been demonstrated,
  activated, and rolled back successfully — and each of them carries its own high-risk review
  and (later) the Trust Core authorization path.
- **Visible result:** from the phone: submit "Add a system clock card to the dashboard" → watch
  the update card move through implementing → testing → candidate-healthy → shows original
  request, changed files, tests run, screenshot of the candidate UI → activate → clock card
  appears → roll back → old UI returns.
- **Minimum components:** update record schema + store (`updates/`, per
  [`DESIGN.md`](DESIGN.md): ID, original prompt, clarified requirements, acceptance criteria,
  target component, candidate slot, worker, timestamps, status, changed files, test evidence,
  health evidence, activation decision/time, rollback state, outcome); update orchestrator
  (drives ClaudeCodeAdapter against the candidate worktree with the update prompt + repo
  conventions); deterministic check runner (unit tests, `/healthz`, smoke); Playwright UI
  evidence check (element present + screenshot); change-scope report (`git diff --stat` of the
  candidate vs active, flagged against the requested target component); update review panel in
  the PWA.
- **Implementation notes:** acceptance criteria live in the update record as a list of
  `{description, check}` where `check` is either a machine-runnable command/probe (exit code /
  HTTP status / Playwright selector) or `manual`. Deterministic checks are authoritative;
  an optional advisory model review (a second model reads request + diff and comments) is
  clearly labeled advisory and can never gate activation on its own. All wording follows the
  evidence-not-proof rule. Backup before activation whenever the candidate touches `state/`
  schema: snapshot SQLite + `updates/` + `secrets/` to a dated archive; Guardian refuses
  activation if the snapshot step fails. **OPEN QUESTION:** how change-scope "unexpected scope"
  is judged beyond path-prefix heuristics — start with path allowlists per target component;
  anything outside → warn, require explicit user acknowledgment.
- **Acceptance tests:** the demonstration itself, scripted in `docs/checklists/m6-clock-card.md`
  and executed for real: steps 1–12 of the minimum self-update demonstration (request → save →
  candidate-only edit → tests → separate start → health check → UI evidence → panel → activate
  → visible → rollback → old runtime returns). Plus unit tests for record store, orchestrator
  state machine, and check runner.
- **Dependencies:** M4 + M5.
- **Review depth:** high-risk (activation/rollback path) — as M5.
- **Deferred:** auto-activation policies, evaluator sophistication, multi-update queues.

## M7 — Natural-language routing and refinement

- **Objective:** free-text control: typed text (later voice) becomes typed actions via the
  three routing levels.
- **Visible result:** type "search YouTube for X and open it on the second display" → the right
  typed action executes; ambiguous requests come back as option cards; "send this to Claude in
  the Cofferdam project" starts an M4 task.
- **Minimum components:** router (level 1: exact/deterministic patterns; level 2: Ollama
  structured-output intent classification against the action schema catalog, JSON-schema
  validated, confidence-thresholded with clarify-fallback; level 3: hand-off to
  `start_or_message_agent_task`); Ollama adapter; router evaluation set (a file of ~50 real
  phrasings with expected actions, run as a deterministic test with a pinned model).
- **Implementation notes:** smallest useful Ollama role = single-shot classification into
  (action, args) with an "unsure" escape hatch; a 3–8B instruct model (e.g. `qwen2.5:7b` or
  `llama3.2:3b`) is plenty. **OPEN QUESTION:** which exact model clears ≥90% on the evaluation
  set on this hardware — settle by running the eval, not by debate. The model output is never
  executed directly: it must validate against a schema and pass the same authorization
  categories as button presses (privileged actions require confirmation regardless of router
  level).
- **Acceptance tests:** router unit tests (level-1 bypasses model; invalid model JSON →
  clarify, never execute); the evaluation-set accuracy test; end-to-end phone demo of the three
  example phrasings in D-2026-08-01-4.
- **Dependencies:** M2–M4 (actions to route to); M6 not required but typically done.
- **Review depth:** normal backend.
- **Deferred:** voice/wake words, memory, Obsidian, multi-turn dialogue planning, Wayland,
  OpenClaw replacement decisions (revisit the register below here).

---

## Remote wake and unattended login (future; approved direction, not scheduled)

Recorded so the shape is not re-litigated later. **None of this is implemented, and none of it
may be started before the milestones above.**

- A **Raspberry Pi 3 B+** becomes the always-on Wake-on-LAN controller for the workstation.
- A **Raspberry Pi Pico (RP2040)** connects directly to the laptop as a bounded USB command
  interface plus HID keyboard.
- **The Raspberry Pi never receives or forwards the Ubuntu password.** It wakes the machine and
  nothing more.
- Once the laptop is reachable, the **phone** sends an ephemeral credential to the Ubuntu
  headless daemon. The daemon issues **one bounded credential-entry request** to the Pico.
- Success is confirmed by a **fresh graphical-session agent registration** — not by a screenshot,
  and not by assuming the keystrokes landed.
- **No permanent password storage.** No HDMI capture is required for the first version. **No
  pixel automation** (D-2026-08-04-7).

## After M7 (later milestones, unordered)

Wayland support; OS-level candidate isolation (separate users/ACLs) and the **Trust Core
re-entry**: wiring the preserved approval boundary in front of privileged actions (package
installs, system config, Guardian updates, destructive migrations); Guardian's own supervised
update path; Obsidian; voice; additional hosts; deeper multi-agent work.

---

## OpenClaw dependency register

Per D-2026-08-01-3. Current entries (updated whenever an OpenClaw capability is adopted):

| Capability | Why | Cofferdam interface | MVP-critical? | Native replacement needs | Removable when |
|---|---|---|---|---|---|
| *(none adopted yet)* | M1–M3 are faster with plain FastAPI + Playwright than with an integration | — | No | — | — |
| Agent session persistence / event streaming | *Candidate*, pending spike before M4: may beat hand-rolling reconnect-safe session plumbing | `AgentSessionAdapter` (task model stays Cofferdam's) | No — M4 is buildable natively | The M4 state machine itself | If spike shows <2 days saved |
| Browser automation / model routing / Ollama integration | *Candidate*, only if the spike shows material lift over direct Playwright/Ollama APIs | `BrowserAdapter` / `ModelRouter` | No | Already planned natively (M3, M7) | Immediately, by design |

~~**Spike (before M4, timeboxed ~1 day):**~~ **SUPERSEDED by D-2026-08-08-5 (2026-08-08).** The
delegated-task lane was built natively — Task Core and its adapters are merged — so there is no
decision left for that spike to settle, and the register above stays at *none adopted*. OpenClaw
remains optional and may later be adopted as a notification, Telegram/WebChat or conversational
**client**; it is never task authority, process authority, a project-path authority, an arbitrary
shell gateway, or part of the Guardian and activation/rollback path. Adoptions are still recorded
in this table with removal criteria.

## Other dependency posture

- **Claude Code** — the first development worker; replaceable adapter (another CLI agent could
  implement updates); practically central for now.
- **Playwright + Chrome** — replaceable adapter, but the expected long-term browser workhorse.
- **Tailscale** — the network boundary; replaceable in principle (WireGuard, local-only), out of
  the app's code path entirely.
- **Ollama** — optional; level-2 routing degrades to level-1 buttons + level-3 delegation
  without it.
- **Remote-desktop fallback** (e.g. Sunshine/Moonlight or RustDesk installed beside Cofferdam) —
  optional escape hatch for raw screen control; never integrated into Cofferdam's code.
