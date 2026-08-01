# Roadmap — personal AI workstation

Seven milestones, ordered to put a visible product on a phone screen as early as possible, then
remote Claude, then the A/B self-update demonstration, then natural-language routing. Review
depth follows the post-pivot policy in [`DECISIONS.md`](DECISIONS.md) D-2026-08-01-6. Items
marked **OPEN QUESTION** are unresolved; each names the experiment that settles it.

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

## M1 — Walking skeleton: phone sees the host

- **Objective:** Cofferdam runs continuously on the Ubuntu host and a phone can securely open
  its dashboard.
- **Visible result:** open `https://<host>` from phone/tablet on the tailnet, enter the device
  token once, see live cards: host up, CPU/mem/disk, Cofferdam version, active slot, uptime.
- **Minimum components:** FastAPI app; static PWA shell (installable, responsive); `/api/status`;
  WebSocket event channel with heartbeat; token auth middleware; systemd user unit + lingering;
  Tailscale-bound listener; `state/`/`logs/` layout from [`DESIGN.md`](DESIGN.md).
- **Implementation notes:** Ubuntu prep is part of this milestone and is documented as a
  runbook (`docs/host-setup.md`): install Ubuntu Desktop, auto-login to an Xorg session,
  disable sleep/suspend, install Tailscale, `loginctl enable-linger`, clone repo, install venv,
  enable units. Guardian does not exist yet — the runtime runs standalone on its final slot-A
  port so M5 can slide Guardian in front without reworking the UI.
- **Acceptance tests:** unit tests for status endpoints and auth (401 without token); manual:
  reboot host → dashboard reachable from phone with no keyboard/monitor touched.
- **Dependencies:** none.
- **Review depth:** low-risk — tests + self-review. (Device-token middleware gets one focused
  look at M5 when activation control rides on it.)
- **Deferred:** Guardian, A/B, any desktop control, HTTPS hardening beyond the tailnet.

## M2 — Desktop hands: apps, processes, screenshots, displays

- **Objective:** semantic control of the Ubuntu desktop from the phone.
- **Visible result:** from the phone: see running apps; open/close Firefox or Chromium; request
  a screenshot (any display) and view it; open a URL on display 2.
- **Minimum components:** typed-action schema base (`actions.py`, versioned, validated);
  adapters: process (`psutil` + launch/terminate), desktop/window (`wmctrl`/`xdotool` wrappers),
  display (xrandr geometry → display registry), screenshot (X11 grab, e.g. `maim`/`import`);
  action log in `state/`.
- **Implementation notes:** every UI button issues a typed action through the same
  `POST /api/actions` executor path that Ollama will later feed — no side channels. Screenshots
  are returned as authenticated API responses, never written under `web/`. Window placement =
  match window by PID/class, move to display-2 geometry via `wmctrl -e`.
- **Acceptance tests:** unit tests for action validation (unknown action/fields rejected);
  integration test on the host: `open_application(firefox)` → window exists; `screenshot`
  returns a decodable PNG per display; `open_url(display=2)` → window's geometry lies inside
  display 2's bounds.
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
  Guardian protocol (small JSON over localhost, allowlisted verbs); PWA reconnect-on-switch.
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
