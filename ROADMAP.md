# Roadmap — personal AI workstation

Seven milestones, ordered to put a visible product on a phone screen as early as possible, then
remote Claude, then the A/B self-update demonstration, then natural-language routing. Review
depth follows the post-pivot policy in [`DECISIONS.md`](DECISIONS.md) D-2026-08-01-6. Items
marked **OPEN QUESTION** are unresolved; each names the experiment that settles it.

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
- **Display/session:** run the desktop as an **Xorg (X11) session**, not Wayland, for the MVP.
  X11 gives working `wmctrl`/`xdotool` window placement, cross-app screenshots, and multi-display
  targeting today; GNOME Wayland restricts screenshots and global window control. Ubuntu still
  ships "Ubuntu on Xorg" at the login screen. Wayland support is a later milestone.
  **OPEN QUESTION:** long-term Wayland path (wlroots protocols / GNOME portal APIs / ydotool) —
  irrelevant until the X11 product works.
- **Browser automation:** Playwright (Python) driving **system Chrome/Chromium with persistent
  user-data dirs** under `~/cofferdam/profiles/`. Netflix DRM (Widevine) requires a real branded
  browser build — use Playwright's `channel="chrome"` with a persistent context. One-time
  manual login by Efe on the desktop persists in the profile; no plaintext passwords in config,
  no passwords through any model.

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
  the host has not been cold-booted since the service was installed, so unattended auto-start
  through lingering, `tailscaled` ordering versus the service's bind, unattended re-binding to the
  Tailscale address, phone access after reboot, and the linger-before-graphical-login capability
  behaviour are all unobserved. M1 therefore **stays "in progress"** and must not be reported as
  reboot-validated or complete. The reboot is deferred at the user's request (the workstation is in
  active use) — deferred, not waived. Closing it means rebooting without logging in, running the
  reboot section of the checklist, and recording the result here and in [`STATUS.md`](STATUS.md).
  Later milestones may proceed while this gate is open only where they neither depend on nor alter
  boot behaviour.
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

**Spike (before M4, timeboxed ~1 day):** stand up OpenClaw, drive one agent session and one
browser action through it, measure integration cost vs the native path. Adopt only what
materially accelerates; record adoptions here with removal criteria. OpenClaw is never in the
Guardian or activation/rollback critical path.

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
