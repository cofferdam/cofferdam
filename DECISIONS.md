# Decisions

The decision record for Cofferdam. Each entry is labeled with who decided and what its current
status is. Statuses:

- **ACTIVE** — governs current work.
- **STILL FROZEN (TRUST CORE MODULE)** — remains binding *inside the Trust Core module*; not a
  constraint on the rest of the product.
- **SUPERSEDED** — replaced by a later decision; kept for history, no longer governs.
- **DEFERRED** — intentionally postponed; not abandoned.
- **OPEN QUESTION** — unresolved; the entry names the experiment or decision needed to settle it.

Decisions marked **EFE DECISION** were made by the maintainer. Recommendations by a reviewing
model are marked as such and are advisory until Efe adopts them.

---

## D-2026-08-01-1 — Product pivot: personal AI workstation (EFE DECISION, ACTIVE)

Cofferdam pivots to an **open-source, personal, always-on AI workstation and remote
computer-control system.**

- First supported host: **Ubuntu Desktop**, running continuously like a personal server, with
  access to the graphical desktop and multiple displays.
- Controlled from phone/tablet (later other clients) through a **Cofferdam-owned responsive
  web/PWA interface.**
- The product must first become useful to Efe personally. **Monetization is no longer a planning
  priority**: no subscriptions, enterprise features, teams, hosted SaaS plans, credit resale, or
  pricing work on the critical roadmap.
- The repository remains open source (Apache-2.0).
- A full custom remote-desktop streaming protocol is **not** a product priority. Semantic
  controls are Cofferdam-owned; an existing remote-desktop tool may serve as fallback for raw
  screen control.
- First-product philosophy: **a small visible product that works**, then the product is used to
  improve itself. Do not begin with Obsidian integration, vector memory, advanced project memory,
  council integration, voice, wake words, native mobile apps, advanced multi-agent orchestration,
  or a perfect security framework — those remain later milestones.

## D-2026-08-01-2 — Required architecture boundaries (EFE DECISION, ACTIVE)

- **Guardian/Supervisor**: a small, stable, non-AI upper layer that starts/stops runtimes, manages
  the A/B slots, health-checks candidates, switches traffic, detects failure, rolls back, and
  preserves logs and update records. Guardian contains no general product intelligence, cannot be
  silently replaced or weakened by the active AI runtime, and has a stricter, separate update
  path. No automatic Guardian self-modification in the MVP.
- **Runtime A / Runtime B**: two replaceable application slots; one active, one inactive/candidate.
  Development work happens only in the inactive candidate; Guardian tests before activation; the
  previous version stays available for rollback. Code, persistent state, secrets, update records,
  logs, and user-controlled files are explicitly separated.
- **Cofferdam Core owns**: the phone/tablet UI, the task and update model, typed action schemas,
  host/device/display state, the Guardian protocol, A/B update state, update history, task cards,
  authorization categories, and adapter interfaces.
- **Replaceable adapters** for: Claude Code, OpenClaw, Ollama/local models, browser automation,
  Ubuntu desktop control, displays, process management, screenshots, media playback, files.

## D-2026-08-01-3 — OpenClaw is optional acceleration, not foundation (EFE DECISION, ACTIVE)

OpenClaw may accelerate agent sessions, tool calling, browser automation, model routing, local
Ollama integration, event streaming, and process/session behavior — but Cofferdam must remain
usable without it once native adapters exist. OpenClaw's internal schemas, files, and session
model are **never** Cofferdam's canonical data model. The adapter boundary must allow moving from
`OpenClawRuntimeAdapter` to `NativeRuntimeAdapter` without rewriting the UI, task records, update
records, action schemas, Guardian, A/B deployment, or user-facing behavior. Removing OpenClaw is
not an immediate goal; an OpenClaw dependency register is maintained in [`ROADMAP.md`](ROADMAP.md).

## D-2026-08-01-4 — Local model routing (EFE DECISION, ACTIVE)

Ollama (or a small API model) may perform short intent classification and structured action
selection, converting natural language into Cofferdam-owned typed actions. The local model is
**not** the operating-system executor and must not invent and execute unrestricted shell commands.
Three routing levels: (1) deterministic commands needing no model; (2) structured intent
classification; (3) development/research work delegated to Claude Code or another capable worker.
Externally prepared prompts (e.g. written in ChatGPT) can be pasted directly into Cofferdam.

## D-2026-08-01-5 — Self-update is the flagship capability (EFE DECISION, ACTIVE)

User-requested product updates flow through stored update records (original prompt, acceptance
criteria, candidate slot, worker, tests, evidence, activation, rollback state, outcome). Workers
operate only on the inactive candidate slot. Deterministic tests are authoritative; model
evaluation is advisory and cannot replace them. Language distinguishes **evidence from proof**
("passed deterministic tests", "matched expected UI evidence" — never "proven correct"). The
first end-to-end demonstration is deliberately small: a system-clock card added to the dashboard,
activated, then rolled back.

## D-2026-08-01-6 — Review policy after the pivot (EFE DECISION, ACTIVE)

Routine council/review ceremony is removed from the normal development loop.

- **Low-risk** (UI, media adapter, status cards, basic browser actions, docs, simple typed
  actions): tests + self-review; no council by default.
- **Normal backend** (process streaming, reconnect, Claude adapter, task state): tests + one
  balanced review only when genuinely useful.
- **High-risk infrastructure** (Guardian, A/B activation, rollback, device authentication, secret
  handling, privileged actions, data migrations, Guardian update path): targeted experiment first,
  one focused architecture review when needed, implementation, one focused implementation review.
  No repetitive council theater; no new gates merely because a question exists.

## D-2026-08-01-7 — Trust Core status (EFE DECISION, ACTIVE)

The existing Trust Core work (PR0–PR3c1 merged; PR3c2 preserved as a WIP commit on its own
branch, incomplete and unmerged — see [`STATUS.md`](STATUS.md)) is **preserved,
not abandoned, and removed from the immediate product critical path.** It is potentially reusable
later for privileged actions and high-assurance updates: dangerous filesystem changes, system
configuration changes, package installation, Guardian updates, root-level operations, destructive
migrations, external data transmission, and exact change-set authorization. Its history is not
deleted or rewritten; its frozen decisions are reclassified below, not silently invalidated.

---

## Pre-pivot decisions, reclassified

| Decision (pre-pivot) | New status |
|---|---|
| Fail-closed, deterministic guard; advisory-cannot-relax (I-3); no `ALLOWED` state; proposal-as-data; hash-bound single-use expiring approvals; I-16 (no user/proposal-controlled subprocess argv) | **STILL FROZEN (TRUST CORE MODULE)** — binding whenever Trust Core code is touched or reused |
| Windows execute unsupported (exit 2; GATE 1C Option B); execution Linux/ext4 only; Git never in the real-write path; pure-transform Candidate B executor; arm-before-consume; no rollback/auto-clear of approvals | **STILL FROZEN (TRUST CORE MODULE)** |
| Zero-network / stdlib-only applies to the whole product ("v0.1 is the only committed scope; nothing leaves your machine") | **SUPERSEDED** by D-2026-08-01-1. Zero-network/stdlib-first remains a property of the Trust Core *module*; the workstation product is inherently networked (LAN/Tailscale UI, model APIs) and will carry audited dependencies. |
| v0.2–v0.6 version lines (provider adapter, Review Room, allowlisted command executor, parity, preferences/lenses, hosted/team/mobile) as the speculative backlog | **SUPERSEDED** as a roadmap by D-2026-08-01-1. Individual capabilities may return later as workstation features; the version-line map no longer governs. |
| Council/premium review gates per PR (handbook `06-review-gates.md`) | **SUPERSEDED** by D-2026-08-01-6. |
| Productization gates: design partner, GitHub Sponsors at v0.1 public, domain/trademark timing tied to v0.1 launch | **DEFERRED** — monetization/productization is off the critical roadmap. Domain/TM remain open human gates with no deadline. |
| Trust Core completion itself (PR3c2 landing, PR3d audit chain, PR4 hardening) | **DEFERRED** — PR3c2 is preserved on its branch (R-1 done); finishing/reviewing/merging it is not on the critical path. |
| Obsidian integration, voice/wake words, native mobile apps, advanced memory, multi-agent orchestration | **DEFERRED** — explicitly post-MVP by D-2026-08-01-1. |
| Windows/macOS *host* support for the workstation product | **DEFERRED** — Ubuntu Desktop is the initial platform. (Trust Core's own platform matrix is unchanged for that module.) |

## D-2026-08-01-8 — License and open development (RECORDED, ACTIVE)

Cofferdam is licensed **Apache-2.0** and is developed in the open. This was already the state of
the repository (LICENSE from the first commit, `Copyright 2026 Efe Aydınalp`); the 2026-08-01
audit confirmed it is unambiguous and consistent across `LICENSE`, `pyproject.toml`
(`license = { text = "Apache-2.0" }` plus the OSI classifier), GitHub's detected license, and
the CI license scan. **No license change was made or is proposed.**

Audit findings recorded as fact:

- **Nothing is vendored.** No third-party source, no bundled/minified assets, no `node_modules`;
  the PWA references zero external URLs (no CDN, fonts, or analytics).
- **The clean-room claim holds.** A content comparison of all 83 Cofferdam source/doc files
  against the 3381 files of the retired prototype found exactly one identical file: an **empty**
  `tests/__init__.py` (0 bytes, no expressive content). No code from OpenClaw, vibe-council,
  `karpathy/llm-council`, or Atticus is present.
- **All runtime dependencies are permissive** (MIT / BSD-3-Clause / PSF-2.0). The one weak-copyleft
  package, `certifi` (MPL-2.0), arrives only via `httpx` in the **test-only** `dev` extra, is not
  a runtime dependency, and is not redistributed.
- **No third-party notices need to be carried** while Cofferdam vendors nothing and distributes
  no bundled dependencies. That changes the moment a wheel/container/installer bundles them —
  see the dependency policy in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## D-2026-08-04-1 — Cofferdam never owns the graphical-session lifecycle (RECORDED, ACTIVE)

Forced by a real regression: the M1 workstation unit made Ubuntu unable to complete a graphical
login. It declared `Wants=graphical-session.target` while being `WantedBy=default.target`, on a
host with lingering enabled. Lingering starts the user manager at boot, `default.target` pulled
the service in, and `Wants=` activated `graphical-session.target` with nothing behind it — so
GNOME refused every subsequent login with "A graphical session is already running!" and bounced
back to GDM. Verified in the journal across four failing boots against one working control boot.

The binding rule:

- Cofferdam **observes and follows** a real graphical session. It never creates, fakes, starts,
  stops, restarts, terminates, or owns one.
- A unit that can start before login must never name `graphical-session.target` in `Wants=`,
  `Requires=`, `Requisite=`, `BindsTo=`, `PartOf=`, or `Upholds=`. `Wants=` is an activation
  request, not a wait.
- Session detection is a **read-only** query (`systemctl --user is-active` / `show`).
- Lingering is never treated as evidence that a graphical session exists.
- No Cofferdam code, unit, script, or recovery command may invoke `systemctl --user exit`,
  `loginctl terminate-user`/`terminate-session`, `gnome-session-quit`, or a broad
  `pkill`/`killall`. Process lifetime belongs to the systemd user manager, which owns each
  transient unit's cgroup.

Enforced structurally by `tests/test_service_unit_lifecycle.py`, not by convention.

Full analysis, migration, rollback, and TTY recovery:
[`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).

## D-2026-08-04-2 — One service, not a daemon/agent split (RECORDED, ACTIVE)

A split into `cofferdam-daemon.service` plus a session-scoped
`cofferdam-session-agent.service` was evaluated while fixing D-2026-08-04-1, and **deferred**.

Rationale: Cofferdam does not launch graphical applications itself. It asks the systemd **user
manager** to start each one as a transient unit, and that manager is what actually holds the real
session environment (GNOME imports `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY` into it at login).
A single always-on unit therefore never has to pretend a lingering process has a graphical
session — it asks, truthfully, every time. The split would not have prevented the regression,
which was a dependency-directive bug rather than a component-boundary bug, and it adds an
authenticated local IPC surface, a registration protocol, and capability-state synchronisation
for no behaviour the current design does not already deliver.

**Revisit if** Cofferdam ever needs to hold live session-scoped resources itself — a compositor
connection, a portal handle, or a persistent browser-automation channel. Not before.

## D-2026-08-05-1 — A capability describes the live session, never our own process (RECORDED, ACTIVE)

Forced by a second real finding on the same host. After login, `/api/status` reported
`screenshot: true` in a GNOME **Wayland** session because `scrot` was installed. The guard that
rejects X11 root-capture tools under Wayland read `XDG_SESSION_TYPE` from the **daemon's own**
environment; a daemon started at boot by lingering has none, because GNOME populates the user
*manager* at login and not an already-running process. The guard therefore never fired, and the
phone offered a Screenshot button whose action could only fail — `scrot: Can't open X display`.

The action itself failed closed, so no black frame and no false success were ever produced. The
defect was the *advertisement*, and it is the same root confusion as D-2026-08-04-1: treating a
process's frozen startup state as if it described the live session.

The binding rule:

- A capability answers "can this action work **in the session that exists right now**", not
  "is a binary present" and not "what did our environment look like when we started".
- Graphical state — session presence, session type, display endpoint, `DISPLAY`/
  `WAYLAND_DISPLAY`/`XAUTHORITY` — is read from the verified session (systemd user manager),
  which is what `detect_graphical_session()` already queries live on every call.
- `os.environ` remains fine for ordinary process setup (`PATH`, `HOME`). It is never evidence
  about the graphical session, and a display variable inherited from an ended session is
  dropped rather than passed to a child.
- Absence of a variable is not evidence of the opposite value: a session publishing
  `WAYLAND_DISPLAY` is Wayland even when `XDG_SESSION_TYPE` is missing. Guessing the permissive
  answer from missing data is precisely what produced the false capability.
- Capabilities are recomputed per request. Nothing is cached across a logout.
- A truthful `false` beats speculative support: no backend is added merely to make a flag true.

Enforced by `tests/test_linux_x11_adapter.py`, whose capability tests run with the daemon
environment emptied, so an implementation that consults `os.environ` fails rather than passes by
coincidence.

Wayland screen capture itself remains unavailable on this host; this decision changes what is
claimed, not what is supported. Detail in
[`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).

## D-2026-08-01-9 — Develop in public from now on (EFE DECISION, ACTIVE)

The GitHub repository `cofferdam/cofferdam` is **public** (Apache-2.0). Before 2026-08-01 only
the Trust Core through PR3c1 had been pushed; the audit surfaced that the pivot documentation,
the M1 implementation, and the preserved PR3c2 executor were still local-only, and that pushing
them would publish them irreversibly.

**Efe decided on 2026-08-01 to push everything publicly**, accepting that this publishes:
the strategic pivot including its open questions, an M1 implementation that is explicitly *not*
validated on its target platform, and the unreviewed, incomplete PR3c2 work-in-progress executor.
This is deliberate: development happens in the open, and honestly-labelled unfinished work is
preferable to a private repository that could be lost with the machine.

Consequences to keep true:

- Every document must keep saying plainly what is validated, what is not, and what is
  incomplete — the labels are what make publishing unfinished work honest.
- Private planning material stays outside the repository and is never committed.
- No secrets, tokens, browser profiles, screenshots, hostnames, or Tailscale addresses in any
  tracked file. This is now a publication guarantee, not a tidiness preference.

## D-2026-08-04-3 — Cofferdam is a local, permission-bounded control plane (EFE DECISION, ACTIVE)

Cofferdam's scope is a control plane for one person's computing environment: the Ubuntu
workstation, future Raspberry Pi guardian/controller nodes, named displays and the human aliases
for them ("büyük monitör"), allowlisted applications and browser profiles, future Claude Code and
other agent sessions, and — later — routing a conversation from a browser to an agent and back to
its originating conversation.

**M2A is the foundation only** (registries, a read-only API, browser-profile-aware `open_url`,
and the architecture documents). It implements none of: Raspberry Pi control, Wake-on-LAN or
physical power actions, window movement or display placement, browser DOM access, ChatGPT/Claude
web automation, browser extensions, agent execution, Claude Code session execution, message
sending, natural-language action planning, desktop application scaffolding, or any reboot
behaviour change. No arbitrary shell execution, at any layer.

The fourteen decisions below are recorded together because they only make sense as a set: each
one is load-bearing for the others. Full rationale is in [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md).

1. **The Python daemon owns authorization, action validation, state, routing records, and
   adapters.** Clients are views and input surfaces. They may ask; they never decide. The daemon
   is the only component that runs unattended and is supervised.
2. **The PWA remains independently usable** even when a future desktop companion is closed,
   crashed, uninstalled, or never installed.
3. **The future desktop companion is a thin UI** for tray status, local approvals, settings, and
   deep links — not a second daemon.
4. **Opera is the preferred personal browser profile; Firefox remains a fallback.** Both are
   selectable through the browser-profile registry.
5. **Opera integration may later use a permission-bounded extension** — as *input* to the daemon,
   never as a place where decisions move to. No extension is built in M2A.
6. **Browser DOM automation is a replaceable adapter and never the core architecture.**
7. **Displays, devices, applications, browser profiles, agent profiles, and route templates use
   stable IDs** — ASCII kebab-case, immutable once referenced, compared exactly.
8. **Human phrases such as "büyük monitör" are aliases resolved through registries** — normalized
   with Unicode case folding plus a Turkish dotted/dotless-I tailoring, never fuzzy-matched, and
   never silently resolved when ambiguous.
9. **Semantic machine configuration belongs in validated registry files, not environment
   variables.** Environment variables stay for runtime knobs (bind address, port, adapter).
10. **Secrets, tokens, credentials, cookies, and browser profile data never belong in these
    registries.** Not by convention — by construction: no schema field can hold them, unknown
    fields fail validation, and a code-owned denylist refuses the obvious attempts by name.
11. **Every future routed task will have an origin, target, correlation ID, return route, status,
    and result.** Recorded now; not implemented in M2A.
12. **Live browser tab IDs and conversation IDs are runtime task state, not static registry
    configuration.** Configuration must not become a session store.
13. **Sending messages, merging code, shutdown, reboot, destructive actions, and physical power
    control require policy-driven confirmation** — driven by policy, not by whichever UI is in
    front of the user. M2A implements none of these actions, and records the rule so the first one
    cannot arrive without a confirmation path.
14. **The post-reboot M1 validation gate remains open and must not be represented as passed.**
    M2A changes nothing about it. See [`STATUS.md`](STATUS.md).

## D-2026-08-04-4 — Thin Tauri desktop companion, not now (EFE DECISION, ACTIVE)

An ADR comparing an installed PWA, a Tauri 2 thin shell, and Electron is recorded in
[`docs/DESKTOP_APP.md`](docs/DESKTOP_APP.md). Decision: **recommend a thin Tauri 2 companion**
(tray status, local approvals, settings, deep links, autostart), keep the Python daemon
authoritative and independent, **add no Rust, Node, or Tauri scaffolding in M2A**, and revisit
implementation in M2B after the registry/API foundation is merged.

The installed PWA is otherwise attractive but cannot provide a tray icon, autostart, or desktop
deep links — and a local approval prompt that only appears when a browser tab happens to be open
is not an approval mechanism. Electron buys predictability with a bundled Chromium and an adjacent
Node runtime, which is a large recurring cost and a standing temptation to move logic out of the
daemon.

## D-2026-08-04-5 — Registries are stdlib-only and read-only in M2A (RECORDED, ACTIVE)

The registry loader (`cofferdam/workstation/registries/`) uses the standard library rather than
pydantic, which the action schemas do use. Two reasons: configuration failures must produce
messages provably free of file content (a registry file could contain anything, and its errors
are returned over the API), which is easier to guarantee with explicit validation than with a
framework's own error text; and it keeps configuration loading importable without the workstation
extras. No database and no YAML parsing is added, and no new third-party dependency.

M2A exposes **no registry write API** — no `POST`, `PUT`, `PATCH`, or `DELETE`. Nothing reachable
over the network can change which applications exist or which domains a browser profile may open.
An atomic writer utility exists with tests, unwired, for the milestone that adds editing.

## D-2026-08-04-6 — Definitions, runtime resources, and user overlays are three layers (EFE DECISION, ACTIVE)

Cofferdam's world separates into three layers, and conflating them makes configuration lie about
the machine:

- **A. Definitions** — code-owned and not configurable: allowlisted application definitions such
  as `opera` and `firefox`, the safe launch adapters, and the bounded executable and desktop-entry
  candidates.
- **B. Runtime resources** — what is actually here right now: connected displays, running
  processes, application instances, windows, later browser tabs, later agent task instances.
- **C. User overlays** — optional names, aliases, preferences, and policy metadata. This is all a
  registry file is.

**Cofferdam must ultimately discover real runtime resources first; registries act only as optional
semantic overlays.** Discover the resource, *then* attach the label — never the reverse.

Consequences, corrected in M2A after the registries were first written the wrong way round:

- **Registries must not pretend to be runtime discovery.** Writing `displays.json` does not make a
  display exist; a browser profile is a launch preference, not an open browser window or process.
- **Nothing ships pre-named.** No `large-monitor`, "main monitor", "small monitor", "laptop
  display", `personal-opera`, "main browser", or "backup browser" in the repository or in a
  default installation. Committed examples are format illustrations whose every id and name begins
  with `example`, and nothing copies them into `$COFFERDAM_HOME`.
- **A machine with no registry files is a fully working machine.** The UI shows honest empty and
  configuration states rather than sample data.
- A discovered display or application instance arrives with **no** user label; one may be added at
  discovery time or at any point later.

### Runtime resource identity rules (recorded now, implemented in the inventory milestone)

- A **PID is visible and usable only with process start-time verification.**
- **PID alone is never a stable resource identity** — PIDs are reused, and a stale PID plus an
  action is how the wrong process gets terminated.
- **Application instance identity** = host/boot identity + PID + start time.
- **Display identity prefers a hardware fingerprint** (EDID, or its hash) plus the owning device.
  **Connector names such as `DP-1` are runtime hints, not identity.**
- **Browser tabs will receive browser-extension tab IDs** and must never be inferred from Chromium
  PIDs.
- **User labels are overlays** and may be attached at creation time or later.

## D-2026-08-04-7 — Semantic interfaces only; no pixel-coordinate automation (EFE DECISION, ACTIVE)

For all later work, prefer: official APIs · CLI protocols · MCP · browser extension APIs · D-Bus ·
systemd · desktop portals · semantic accessibility interfaces.

**Mouse-coordinate and screen-pixel automation is not an accepted core mechanism**, and none
exists in the product. Clicking at (x, y) is unverifiable, breaks silently on any layout or
resolution change, and reproduces exactly the class of false success that M1 was spent
eliminating. Where a semantic interface genuinely does not exist, the correct answer is to report
the capability as unavailable.

Related, recorded so the inventory milestone does not adopt a wrong assumption:

- **Cursor is not a way to access or continue an existing ChatGPT consumer conversation.**
- **Cursor CLI is a future target-agent adapter**, in the same category as Claude Code.
- Existing ChatGPT conversations will connect to Cofferdam through **a ChatGPT App/MCP tool** for
  explicit task dispatch and result retrieval, and/or **a permission-bounded Opera companion
  extension** that associates a tab/conversation with a task and prepares the returned result in
  the same conversation for a human to confirm.
- None of Cursor, MCP, or the Opera companion is implemented in M2A.

## D-2026-08-05-2 — Runtime discovery backends on GNOME Wayland (RECORDED, ACTIVE)

Chosen after read-only investigation of the real host (Ubuntu, GNOME Shell 50.1, Wayland). Each
choice is recorded with the alternative it rejects, because the rejected ones all look adequate
until they are wrong.

- **Displays: `org.gnome.Mutter.DisplayConfig.GetCurrentState`, joined to `/sys/class/drm`.**
  Not `xrandr`. Under Wayland `xrandr` talks to XWayland, which reports a *synthetic* layout kept
  for X11 clients — derived from the compositor's configuration but not it. M1 took only a display
  *count* from it, and a count was the most it could honestly support. The kernel supplies the raw
  EDID (the fingerprint) and the physical millimetres, which `GetCurrentState` does not report;
  the compositor's `GetResources` would, but it is the deprecated interface, so sysfs is read
  directly.
- **The two sources are joined on the panel's own EDID-derived `(manufacturer, model, serial)`
  triple**, not on connector names — the kernel says `card1-HDMI-A-1` where Mutter says `HDMI-1`.
  Content matching is exact; a hand-maintained name mapping is a guess. Name matching stays as a
  labelled fallback.
- **Processes: `/proc`, read directly.** Not `ps`: every layer between us and the kernel's own
  files is a layer that can reformat, truncate, or localise them.
- **`/proc/<pid>/cmdline` and `/proc/<pid>/environ` are never opened.** Not read-then-redacted.
  Both routinely carry secrets on a real desktop, and safely handling a secret already in memory
  is a much harder problem than not reading it. Grouping therefore uses cgroup membership and
  process ancestry, which need neither.
- **Application instances: systemd cgroup scopes under `app.slice`, plus our own
  `cofferdam-app-*.service`.** The system already computed the boundary — all nineteen of Opera's
  processes share one `snap.opera.opera-<uuid>.scope`. A `.scope` is what systemd creates for a
  process it did not fork itself, i.e. a launched application; the plain `.service` units also in
  `app.slice` (`dconf`, `ssh-agent`, `gnome-keyring-daemon`, this service) are infrastructure and
  are excluded. Units are merged on systemd's naming grammar, so the two scopes GNOME creates for
  one launch are one instance.
- **A definition match requires the exact basename of the root process's real executable.** Not
  `comm` (a 15-character truncation a process can rename at will), not a substring (`operator` is
  not Opera), and not any member's executable (an Electron application shipping a `chromium`
  binary would otherwise be reported as Chromium). No match leaves the instance unmapped, which is
  a complete answer.
- **Windows: no backend exists, and the collection reports `unavailable`.**
  `org.gnome.Shell.Eval` returns `(false, '')` on this host — disabled outside unsafe-mode — and
  is barred by D-2026-08-04-7 regardless, since evaluating JavaScript inside the compositor is
  arbitrary code execution in the user's shell. No portal enumerates windows. The AT-SPI bus runs
  but `toolkit-accessibility` is `false`, and switching it on is a change to the user's desktop
  with a real cost, not a decision this milestone makes on their behalf. Installing a GNOME
  extension is a persistent change the user has not asked for. The seam stays open for a companion
  extension the user installs knowingly.

## D-2026-08-05-3 — `unavailable` is not `empty`, and the model enforces it (RECORDED, ACTIVE)

Every runtime collection reports one of `ok`, `partial`, `unavailable`, `error`. An `ok`
collection with zero items is a **positive claim** that the machine has none of that resource. A
backend that cannot answer reports `unavailable` with a reason and carries no items.

This is enforced in the model rather than by convention: constructing an `unavailable` or `error`
collection that carries items, or that omits a reason, raises. The PWA checks the `unavailable`
branch *before* the empty branch, because an unavailable collection has zero items too.

The rule exists because the same false-success shape has now been found three times in this
product — a launcher's exit code taken as evidence a window opened (M1), a registry read as a list
of connected hardware (M2A), and "Firefox available" read as "Firefox running" (PR #9 validation).
Telling a user their applications have no windows open, while they are looking at those windows,
would be the fourth.

## D-2026-08-05-4 — Runtime inventory is read-only; control is a separate milestone (EFE DECISION, ACTIVE)

M2B observes. It starts, stops, moves, reconfigures, and terminates nothing, and no route under
`/api/runtime` accepts a write method.

Process and window **control** — closing an application, moving a window to a named display — is
deferred to its own milestone, because it needs something discovery does not: **re-verification of
identity immediately before acting.** A PID plus a start time captured a second ago is evidence
about a second ago. `start_ticks` is published precisely so that check can be made, and shipping
control without it is how the wrong process gets terminated.

Label and alias **editing** is likewise deferred, to the immediate M2B2 follow-up. M2B resolves
overlays that already exist onto discovered resources; every resource carries a stable
`resource_id` and an `overlay` slot, so editing needs no change to the identity model.

## D-2026-08-05-5 — Opera is Cofferdam's default browser, inside Cofferdam only (EFE DECISION, ACTIVE)

Cofferdam opens generic links, and every media web service, in **Opera**. Firefox stays a
first-class, explicitly selectable browser — by profile, or by `browser_id` on the action itself —
and no Firefox definition is removed.

The scope of the word "default" is the point:

- It is a preference **inside Cofferdam**. Nothing reads or writes the desktop's default-browser
  setting, and no file association is changed. (On the validation host the OS default already
  happens to be Opera; the product default is deliberately not derived from it, so the two stay
  independent.)
- It sits **below** both configured paths. An explicit `browser_profile_id`, an explicit
  `browser_id`, and a registry profile marked `default_for_url` all outrank it. It answers only
  "nothing is configured — what should a link do?", where the previous answer was whichever browser
  sorted first in the adapter's table: an implementation detail standing in for a decision.
- It **degrades** to the pre-M2B3A behaviour on a host without Opera, rather than failing on a
  preference that cannot be honoured.

It is implemented in `browser_selection`, **not** by reordering the adapter's browser table. That
table is still the last-resort "first installed browser wins" answer, and editing it would have
changed the fallback itself rather than layering a preference above it.

A new `browser_id` action field selects a browser directly, so "open this in Firefox" no longer
requires writing a registry file first. It is mutually exclusive with `browser_profile_id`, and it
does **not** escape a configured allow-list: when an enabled default profile exists, that profile's
domain policy still binds whichever browser is named. Otherwise naming a browser would have become
the way around the policy.

## D-2026-08-05-6 — Media providers are a code-owned catalogue, and no wrapper is installed (EFE DECISION, ACTIVE)

Spotify, YouTube, Netflix, Prime Video and TV+ are reachable from the phone as **launch
definitions**, not as integrations.

- **Spotify** is the real installed desktop application. Search hands it a `spotify:` URI, which is
  an entry point the installed application registers for on this host
  (`MimeType=x-scheme-handler/spotify`) rather than a trick.
- **Netflix, Prime Video, TV+ and YouTube** are represented as web services opened in Opera. **No
  unofficial Electron wrapper, and no third-party Snap or Flatpak that merely repackages a website,
  is installed or required.** App-mode Opera windows were investigated and rejected: the installed
  build (Opera 133, snap) exposes no `--app` switch, so Cofferdam opens an honest dedicated window
  rather than claiming a standalone app.
- The catalogue lives in **source**, not in a registry. A media provider *is* a URL, and the M2A
  registries deliberately cannot name one — putting providers in a JSON file would hand that file
  the power to aim a browser anywhere. A client sends a provider id from the allowlist and, at most,
  a bounded search phrase; it has no vocabulary for a URL, a template, a parameter name, or a
  scheme.

**Two things are refused rather than faked.** No action claims playback: opening Netflix opens a
page and searching Spotify opens a search, so every media result reports `playback: not_started` on
success. And **TV+ ships without search**, because its unqualified search address redirects to the
storefront root and discards the query — a "search" built on it would open the home page while
reporting success, which is exactly the false success M1 established as unacceptable. The card says
so, with the reason.

Playback control, catalog search with real result cards, and DOM-level service search are deferred
to the adapter seams documented in [`docs/MEDIA_PROFILES.md`](docs/MEDIA_PROFILES.md). Safe
close/restart of application instances remains M2B3B.

## D-2026-08-05-7 — The server, not the client, converts a result into an open action (EFE DECISION, ACTIVE)

Structured search returns **opaque handles**. To open something, the client names a search session
and a result; the server re-resolves both from its own memory and rebuilds the launch target from
validated identifiers.

The rejected alternative is the obvious one: send each result's `spotify:` URI or watch URL to the
phone with the card, and let the phone send it back on tap. That is one fewer moving part, and it
would have made Cofferdam accept a caller-supplied URI — the exact capability the typed-action
boundary exists to withhold. No request schema in this milestone has a field for a URL, a URI, or a
video id, and unknown fields are refused rather than ignored.

Three consequences follow, and each is enforced rather than promised:

- **Search sessions are bounded and in-memory.** 600 s TTL, 32 concurrent, 5 results each, no
  persistence. They die with the process, which is honest — a restarted daemon that still honoured
  old `search_id` values would be claiming knowledge it no longer has — and it means a record of
  what someone was looking for does not outlive the moment they were looking.
- **A result cannot be opened through another provider.** The client asserts a provider id and the
  server refuses when it disagrees with the session. Without that check, a caller holding a valid
  search id could route a YouTube video id into the Spotify native-URI adapter.
- **Targets are rebuilt, never forwarded.** The Spotify URI is reconstructed from a validated type
  and base-62 id; the YouTube watch URL from a constant prefix and a validated 11-character id.
  A forwarded string would mean the value handed to a native application came from a network
  response.

**Opening the first result is an explicit button, never automatic.** Provider ranking is an
opinion, and acting on it unasked is how the wrong song opens. The persistent auto-open-first
preference is **deferred**: it needs a settings surface this milestone does not have, and the
capability reports `auto_open_first_supported: false` so the phone need not guess.

## D-2026-08-05-8 — Official provider APIs only, with credentials that never leave the host (EFE DECISION, ACTIVE)

Structured results come from the **Spotify Web API** and the **YouTube Data API v3**, or they do
not come at all. No scraping, no DOM automation, no browser-profile or cookie inspection.

**Credentials live in `$COFFERDAM_HOME/secrets/media_providers.json`** (0600, in the existing 0700
secrets directory) — the same place as the device token. No new mechanism was invented, because the
repository already had a reviewed answer to "where does a local secret go", and a second one is how
a project ends up with a secret in the place nobody audits.

**There is no credential form in the PWA.** Typing a key into the phone would put the secret in a
request body, a text input, and a file the web tier can write. There is no reviewed secure
secret-entry mechanism over the network in this repository, and this is not the milestone to invent
one. The only thing observable anywhere is a **status word** — `configured`, `missing`, `invalid`,
`provider_rejected`, `temporarily_unavailable` — never a value, prefix, length, hash, or even the
credential file's path.

Two properties are structural rather than reviewed:

- **Playback control is unreachable, not merely unimplemented.** Spotify's client-credentials flow
  reaches only endpoints that do not access user information, so the token Cofferdam holds cannot
  call a playback endpoint. YouTube uses an API key with no user scope at all.
- **The network layer cannot be redirected.** One module talks to the internet, over stdlib
  `http.client` with a fixed host allowlist, verified TLS, bounded timeouts and response size, and
  **redirects that are never followed** — a 3xx is a failure, not a hop. It reads no proxy
  environment variable, so nothing outside the code can steer an outbound request.

**Nothing claims playback.** Opening the exact track opens it; every media result reports
`playback: not_started` on success and the phone repeats that wording.

**Absence is a normal state.** With no credentials the phone says "structured results not
configured" and the M2B3A Open and Search-page actions keep working untouched — never a broken
enabled control, and never a fabricated result.

Netflix, Prime Video and TV+ publish no official catalogue-search interface for this purpose and
are unchanged. Their catalogue entries carry no adapter key, so they cannot acquire structured
search even if the credential store were somehow told they were configured. That case is deferred
to M2B3A.2 — Opera Companion foundation.

## D-2026-08-05-9 — Audio resources are graph-scoped, and a PipeWire node id is never authority (EFE DECISION, ACTIVE)

M2C is the first part of the product that changes the **physical** state of the machine. Three
rules follow from that, and they are binding on every later audio milestone.

**A PipeWire node id is an address, not an identity.** The daemon hands out small integers and
**reuses them once the object they named is destroyed**, so node 58 is the built-in speaker today
and could be a Bluetooth headset after a restart. An output is therefore addressed by a
`resource_id` digested from host + audio-graph cookie + the sink's stable node name, and the node's
name *and* PipeWire `object.serial` are re-verified against a fresh graph read immediately before
acting. A node id being present is not enough; it must still be the same object. The client never
sends a node id, and no code path accepts one as authority. A separate `stable_id` omits the graph
so the later preferred-output overlay has a key that survives restarts.

**One volume scale, and it is the one the user can see.** PipeWire stores gain linearly; `wpctl`
and the desktop's own slider use a cubic perceptual scale — the development host read `0.846138`
linear and `0.95` through `wpctl`. Publishing the linear figure would put 85% on the phone for a
speaker the laptop calls 95%. Volume is therefore read *and* written through `wpctl` only, and no
curve is assumed anywhere in the codebase. Mute is read from the graph, where it is an unambiguous
boolean, which keeps the verification independent of the tool that performed the write. The product
range is 0–100 and amplification above unity is not offered; out-of-range input is **refused, never
clamped**, because a client asking for 150 has a bug and quietly giving it 100 hides that bug.

**An accepted command is not an applied change.** `wpctl` exits zero for anything it accepts, so
every action re-reads the host afterwards and reports observed state, with `requested` and
`observed` as separate keys. Selecting a default output reports what the streams actually did:
whether already-playing audio follows is WirePlumber policy, and the honest answer is "the default
moved, this stream did not" rather than a clean success.

**`move_audio_stream` is refused, not implemented.** WirePlumber on this host exposes no command
for it — `wpctl` has `set-default`, `set-volume`, `set-mute`, `set-profile` and `set-route`. It
could be done by writing PipeWire metadata keyed by the stream's *transient node id*, which is
exactly the identity above, and WirePlumber's `node.stream.restore-target` would then persist that
choice and pin the application to that output for future sessions — a lasting change nobody asked
for. The capability is published as `unavailable` with that reason. **A shell command accepting two
numeric ids is not evidence that an operation is safe.**

**Streams are named only on evidence the application did not supply.** `application.name` is a
string a client chooses. The association uses `pipewire.sec.pid`, which the daemon writes from
socket peer credentials and a client cannot forge, resolved through `/proc` to an **exact**
executable match. Anything short of that stays unclassified with a reason: telling someone Spotify
is playing when it is not is worse than saying "unidentified". Published stream fields are an
**allowlist**, never a filtered property bag, so `media.name` — the track or video title — cannot
leak through a key nobody thought to ban.

**System volume and player volume stay separate.** The output level belongs here; Spotify's
playback volume belongs to the Spotify Playback milestone and a YouTube player's volume to its own.
Two controls both labelled "volume" that mean different things is exactly the ambiguity a control
panel must not create.

## D-2026-08-05-9 — Spotify playback is a *user* authorization, kept apart from everything else (EFE DECISION, ACTIVE)

**Decision.** Controlling the user's own Spotify player is built on Authorization Code with PKCE,
completed once in a browser **on the workstation** against a loopback redirect, with the resulting
refresh token stored locally under owner-only permissions and kept in a different file from the
catalogue-search credential. Verified against the official Spotify developer documentation on
2026-08-05 and recorded in [`docs/SPOTIFY_PLAYBACK.md`](docs/SPOTIFY_PLAYBACK.md).

**Three different kinds of power, three different modules.** The catalogue-search credential is an
*application* credential that says nothing about any person and can only read a public catalogue.
This is a *user* credential: proof that a human let Cofferdam change what they are listening to. And
the audio milestone controls a *machine*: this computer's speakers. Deleting the Spotify OAuth file
disconnects an account; deleting the catalogue file turns off search; neither touches PipeWire. They
are separate packages, separate files and separate PWA panels, because a user with two sliders
labelled "volume" cannot tell which one made the room go quiet.

**PKCE, because it needs no secret.** Spotify's current documentation recommends it wherever a
client secret cannot be safely stored, and the token exchange carries none. That is the reason it is
used rather than plain Authorization Code: the catalogue secret already on this host never travels
anywhere near the authorization path, so nothing in that path can leak it.

**The callback is loopback-only, and that is structural.** Spotify's redirect rules permit HTTP for
a loopback address and refuse `localhost`, so the registered URI is `http://127.0.0.1:8888/callback`
and the temporary listener binds to `127.0.0.1` — a module constant, not configuration, and the
constructor raises on anything else. It serves one path and answers everything else with 404 without
reading a query string. Binding it to the Tailscale address instead would make the registered URI a
lie *and* put an authorization endpoint on a network. **`127.0.0.1` on a phone is the phone**, so
the flow is workstation-bound by construction and the PWA says so rather than leaving someone
waiting for a tab that cannot arrive.

**Absence of a refresh token means keep the one you have.** The PKCE documentation states that a
refresh response "might not include a new refresh token". Reading that as "the token is gone" would
disconnect a working account at the next restart, and it would look like the user's fault.

**A device id is not an identity, again.** Spotify documents its device id as persistent "to some
extent" and allows it to be `null` — the same trap as a PipeWire node id, and the same answer: the
client holds an opaque host-scoped handle, the provider id stays server-side, and the handle is
re-resolved against a freshly read device list before every targeted action. **No fallback to
matching a device by name**, because two speakers can share one and a name is something someone
typed into a phone once. Device handles are not persisted as preferences in this milestone, because
they are not stable enough to be one.

**Mute is volume zero, and the product says so.** Spotify publishes no mute operation anywhere in
its player API, so the flag is named `muted_by_cofferdam` — never `muted` — and the panel states the
mechanism in plain words. Unmuting is then a question Spotify cannot answer, so the level is
remembered locally in `state/`, deliberately outside the credential file. When no level is known —
a fresh install, a cleared state directory, a mute performed in the Spotify app itself — **unmute
refuses and asks the user to pick one**. Restoring to 50% "because that is reasonable" would be
Cofferdam deciding how loud somebody's speakers get.

**A 204 is an acknowledgement, not an outcome.** Every player write returns `204 No Content`, and
the documentation warns that execution order is not guaranteed across player endpoints. So every
action re-reads playback and reports what it observed, and playing a chosen track verifies that the
item now playing is the item that was requested. Queueing is the one operation that reports
`accepted_by_provider` rather than an observation — and it explicitly does not claim playback
started, because the current track is expected to keep playing.

**A track is named by which search result it was.** Play and queue take the search id and result id
the server issued and nothing else; the server rebuilds the `spotify:track:…` URI from the private
item in the existing session. There is no request field for a URI, a track id, a device id or a URL
to validate — they are *absent from the schema*, which is a stronger guarantee than a rejection. The
existing cross-provider and session-expiry checks do the rest.

**Playback state is personal activity, so the audit is deliberately thin.** What someone listened
to, when, and how often is a detailed picture of a person, and an action log carrying track titles
would quietly become a listening history: kept on disk, shown in a list, and never asked for. The
audit records the operation and the outcome and nothing else. The authenticated PWA may show the
current track — it is the point of the panel — and nothing else may.

## D-2026-08-06-1 — One Cofferdam-owned YouTube player, and Cofferdam owns its queue (EFE DECISION, ACTIVE)

**Decision.** YouTube playback is driven through a **single Cofferdam-served player document**
embedding one official IFrame Player API player, opened once in Opera on the workstation and
controlled over a **loopback-only** channel with a closed message vocabulary. Cofferdam keeps its
own play queue and never uses YouTube's playlist queue. Verified against the official IFrame Player
API and embedded-player-parameter documentation on 2026-08-06 and recorded in
[`docs/YOUTUBE_PLAYER.md`](docs/YOUTUBE_PLAYER.md).

**Why not control a normal watch page.** The previous behaviour opened
`https://www.youtube.com/watch?v=…` per selection. Cofferdam does not own that page, so there was
nothing to control, every video was a new tab, and a tab appearing was the only available evidence
of success — which is not evidence of playback at all. Controlling a page Cofferdam does not own
would mean DOM automation, which D-2026-08-04-7 already rules out. Serving our own minimal document
is the option that gives real control without any of that.

**Why the queue is not YouTube's.** `nextVideo()`, `previousVideo()` and `playVideoAt()` are
documented *only* against a YouTube playlist; there is no defined behaviour when none is loaded.
Handing YouTube an array of video ids would work and would also hand it the ordering, the
advance-on-end behaviour and the loop/shuffle state. The one thing this product must never do is let
a **recommendation** become the next video, and a queue whose contents Cofferdam cannot enumerate is
not a queue it can honestly report. So Next is a Cofferdam decision implemented with
`loadVideoById`, bounded, in memory, and it refuses when nothing is queued rather than playing
whatever would have come next.

**Why the player page carries no token.** A long-lived credential in a browser tab lives in that
tab's history and in whatever the browser syncs — a worse thing to hold than the problem it solves.
The page is authenticated by *where it can reach*: a second listener bound to `127.0.0.1` as a
module constant, never the tailnet. Stated honestly, that boundary grants nothing new — a process
running as this user could already read the token file — so the work is in keeping out everything
that is *not* a same-user process: a Host-header check against DNS rebinding, an
`application/json` requirement that forces a preflight no CORS header ever answers, fixed paths, and
bounded bodies, connections and polls. This is the same shape as the M2D OAuth callback listener,
and the second use of that pattern.

**The channel is closed in both directions.** Cofferdam may send five commands; the player may say
three things. A player page has no message that starts an action, opens an application, or reaches
any other part of the workstation API. A compromised player page gets to lie about what is playing;
it does not get a foothold.

**A player is a document that is currently saying so.** Connection state comes from a heartbeat, not
from Opera's process list, because Opera is one process with many tabs that outlives any of them.
This is the same rule as D-2026-08-04-6's runtime identity work and D-2026-08-05-9's refusal to
treat a Spotify device id as an identity: a signal that is *usually* right is the most dangerous
kind, because it works in testing.

**Autoplay is reported, not defeated.** The browser refusing to start unmuted audio is a documented
browser policy, surfaced through the API's own `onAutoplayBlocked` event. Cofferdam exposes it as a
state with the chosen video left cued, sends `playVideo` once rather than looping, and never starts
muted and calls that success. One click on the player window resolves it for the session, because
media autoplay requires *sticky* activation, which is never consumed.

**Viewing is personal activity, so the audit is deliberately thin.** Same reasoning as the Spotify
decision above, and the same conclusion: the audit records the operation and the outcome and nothing
else — no video, title, channel, query or queue content. The loopback listener silences its own HTTP
access log, whose request lines would otherwise become a timestamped record of when somebody was
watching something. The authenticated PWA may show the current video and the queue; nothing else
may, and nothing is stored.

**The player page identifies itself, and privacy hygiene is not free** *(added 2026-08-06 after
real-host validation)*. The first shipped player sent `Referrer-Policy: no-referrer`, carried a
matching `<meta name="referrer">`, and set `referrerpolicy="no-referrer"` on its iframe. Each of
those looked like good hygiene. Together they broke playback outright: an embedded player must be
able to say **which page is embedding it**, and YouTube answered every embed with error `153` —
"Video player configuration error" — while the page itself reported connected and healthy.

The correction is `strict-origin-when-cross-origin` on the response, no meta tag at all, and the
same policy named explicitly on the iframe, because a per-iframe policy overrides the document's in
both directions. The `origin` player parameter is built **server-side** from the loopback constant
and the port actually bound — a different port is a different origin — and no client can supply or
influence it, because there is no request field it could arrive in.

What this actually discloses is one line: that a page on this machine's loopback interface embedded
a player. Not the video, not who, not from where. Set against a feature that does not work at all,
that is the right trade — and it is worth recording that the wrong one was chosen first, looked
principled, and was caught only by running the thing on a real host.

**Error 153 gets its own state.** Before the mapping existed, a rejected embed reached the phone as
"the video is loaded and has not started" — a sentence that reads as a slow video and invites
waiting. It is now `youtube_embed_client_identity_rejected`: deliberately not `autoplay_blocked`
(there is no loaded player to click), not "video unavailable" (the video is fine on the normal
page), and never a success. The phone explains it and offers exactly two things — retry the
dedicated player **once**, or Open in YouTube. The retry is bounded because a configuration state is
not a race, and a button that can only fail again is worse than no button.

## D-2026-08-06-2 — Task Core is provider-neutral, and adapters are the only place an agent exists (EFE DECISION, ACTIVE)

**Decision.** Agent work is modelled as a **task**: a durable, server-identified unit with a strict
state machine, an append-only event history, an authenticated API and a phone UI. Everything
specific to a *particular* agent — Claude Code, Cursor, a local model, a remote provider — lives
behind a provider-neutral **adapter** interface and nowhere else. Recorded in
[`docs/AGENT_TASK_CORE.md`](docs/AGENT_TASK_CORE.md).

**Why the foundation comes before the integration.** The boundary between "what a task is" and "how
Claude Code runs one" is much easier to draw before a real integration's process handling has grown
roots through the middle of it. Every previous milestone that mixed the two — a tab per video
standing in for playback state, a Spotify device id standing in for an identity — had to be
untangled later. So this milestone ships a complete task system and deliberately **no way to run a
real agent in it**, and a test scans Task Core for the name of any specific integration and fails if
one appears.

**A task is not a process, and the client cannot name one.** There is no field for a working
directory, an executable, argv, an environment, a shell string, a pid or a unit name — not validated
and rejected, *absent*. A request names a **project id**, and the server resolves it to a verified
root from a host-owned registry, re-checking for symlink escapes immediately before use. The most
dangerous field an agent API could have is a path, so it does not have one. The prompt is content
for an adapter, never an OS command, and no text produced by a model is authority for anything on
this machine.

**The state machine is enforced in one place, transactionally with its event.** A snapshot that says
`completed` with no completion event is a history that disagrees with itself, so both are one write.
That requirement is what made SQLite the honest choice here, after four milestones where an
atomically-replaced JSON file genuinely was — `store.py` had already written down that tasks would
be where that stopped being true. It adds no dependency: SQLite is in the standard library, so the
stdlib-only CI path is unchanged.

**A row that says `running` is not evidence that anything is running.** After a restart, every
non-terminal task becomes `interrupted` — never resumed, never left claiming to run, previous output
preserved, terminal tasks untouched. Not because resuming is hard, but because resuming something
whose state nobody observed is how a task system starts lying. `interrupted` is deliberately a
different word from `failed`: the task did not go wrong, Cofferdam went away underneath it, and
telling somebody their work failed when it was never given a chance would send them debugging
nothing.

**Manual-first, and meant permanently.** A person chooses the project, the adapter, the prompt, and
every follow-up and cancellation. There is no routing, no planning, no autonomous loop and no model
call anywhere in Task Core. Cancellation is a message to *one* adapter about *one* task — there is
no `pkill`, no signal, no process-name matching anywhere in the package — and an operation an
adapter cannot do is a truthful refusal rather than a silent success.

**The validation adapter is not an agent, and is off by default.** Validating a lifecycle end to end
on a real host needs something to drive it, and driving it with a real model would mean the first
run of this system did something nobody asked for. So a deterministic adapter emits a fixed
code-owned sequence and reaches a fixed state; it runs no program, calls no model, writes nothing,
and imports nothing that could. It is registered **only** when the host was explicitly configured to
allow it — a flag, a config key or an environment variable, never a client — and when it is off the
object is never constructed. A default install after this merge has an empty adapter list, which is
the honest state of a foundation milestone rather than a fault.

**Secrets are reserved in vocabulary and absent in mechanism.** Waiting for a password, a one-time
code, a push approval or a passkey are named now so that the day an adapter needs to say one there
is a truthful word for it. None is implemented, and there is no secret-input form to misuse: when
they are supported they must use a dedicated ephemeral channel and must never reach a prompt, a task
history, an event or an audit record.

**What a task says is somebody's private thinking.** Prompts, follow-ups and results are stored,
shown to one authenticated client, and kept out of everything else: Task Core contains no logging
call at all, the panel contains no `console` call, content never enters a URL, an argv, a unit name,
a task id or an audit record — and the audit function has no parameter for content, which makes that
a property of the signature rather than a habit every caller has to keep.

## M2G — Claude Code adapter (2026-08-06)

**A prompt is content, not authority.** The phone sends a project id, an adapter id and text.
Everything that decides what actually runs — the executable, every argument, the permission
profile, the tool set, the environment, the session id, the working directory — is a constant in
one file. There is no request field for any of them, and a test reads the route's own allowlist to
prove the client vocabulary is five keys long.

**The architecture came from the installed CLI, not from memory.** Two probes against a disposable
sandbox settled it: one `claude -p` with `--input-format stream-json --output-format stream-json`
took a second user message on the same stdin and answered with the same `session_id`, staying alive
between turns; and SIGTERM to its process group ended a run in 0.4s with exit 143. So one bounded
long-lived process per task. `--resume` also works and was rejected: continuity would then rest on
a session store Cofferdam does not own, and "cancel" would have to mean "remember not to start the
next turn", which is a weaker promise wearing the same word.

**Bash is not in the tool set, and that is the containment argument.** Not gated behind an approval
— absent. A Bash tool inside an approved project root is still a general shell on the workstation,
reachable by writing English into a phone. The probe confirmed the CLI answers "no Bash tool is
available" rather than prompting, so the refusal happens before anyone has to decide anything.
`--strict-mcp-config` with no config and `--setting-sources ""` mean no file on this machine can
widen the profile afterwards, which is also why Cofferdam never writes a Claude settings file.

**Cofferdam grants reachability, never possession.** The child gets a thirteen-name environment
allowlist built by selection rather than copy-and-delete. `HOME` is in it because the CLI's
subscription login lives under the home directory; Cofferdam does not read those credentials, does
not know their format and does not name their path. No `ANTHROPIC_*` variable, no key, no token.
The auth probe is one fixed command and reads exactly two fields — the email address and
organisation id in the same response are never assigned to anything, so there is no attribute for a
later refactor to log by accident.

**A pid is not an identity.** A run is a pid *and* a `/proc` start time *and* a process group *and*
an adapter run id, re-verified before every signal — including between SIGTERM and SIGKILL, because
a process can exit and its pid be reused in that gap. Nothing matches a process by name. If
identity is lost, nothing is sent and the task says so; if the process will not stop, the task stays
`cancelling` rather than being promoted to `cancelled`.

**Exit code zero is not success.** Completion needs a `result` frame with `is_error` false and text
in it. A process that ran and reported nothing is a failure that says exactly that. `approvals` is
`False` because this version cannot answer a permission request, and `recover_after_restart` is
`False` because no recovery is implemented — neither is "possible later" written as true today.

**Two things the foundation declared and left unbuilt are now built, generically.** `inspect()` had
no caller, which was fine for an adapter that finishes inside `start()` and unworkable for one whose
work happens in a process; `TaskService.refresh_task` is that call, and it is deliberately the only
mechanism, because a callback or a background sweep would be a second path that writes task state.
And `AdapterEvent` promised evidence is a claim "unless the core observed the thing itself" with no
way to say so; `AdapterOutcome.observations` is that channel, still checked against
`VERIFIED_EVIDENCE_SOURCES`, so an adapter cannot launder a claim by moving it — only fail to be
believed.

**The authentication wait shows a sentence, not a field.** The foundation named
`SECRET_BEARING_WAITING_REASONS` and the panel never read the list, because no adapter had produced
one. This one does, and a textarea labelled "Your answer" under "waiting for sign-in" is an
invitation to type a password into a task history. Cofferdam does not want the secret and has
nowhere to put it, so it says so and points at the workstation.

## D-2026-08-08-1 — A private Custom GPT with Actions is the ChatGPT-facing client (EFE DECISION, ACTIVE)

**Decision.** The primary ChatGPT-facing client for Cofferdam is a **private Custom GPT calling
bounded GPT Actions** against a Cofferdam-owned HTTP bridge. It is a *client* — replaceable,
untrusted, and holding no authority — in exactly the sense D-2026-08-04-3(1) already requires of
the PWA: it may ask, it never decides.

**The capability was probed before it was planned into a milestone, and the probe passed on
2026-08-08.** An isolated echo service outside this repository — its own directory, its own port,
its own bearer credential, a temporary Cloudflare Quick Tunnel in front of it — was called
successfully by the desktop GPT Builder (`GET /health`, then the consequential `POST /echo`) and
then by the **native iPhone ChatGPT application** (the same two calls), with the response returning
into the same private conversation. The mobile call carried `client_test_id = mobile-app-1` and the
server recorded `seen = 1`, `duplicate = false`: one accepted mobile confirmation produced exactly
one server invocation, which is the property that matters for an Action that creates a task. The
Cofferdam repository, the workstation daemon, the PWA, Task Core and the systemd configuration were
not modified, not exposed and not involved.

**What that probe does and does not establish.** It establishes that a private Custom GPT can reach
a personal workstation service from a phone, with bearer authentication, including a consequential
action requiring confirmation. It establishes nothing about production transport reliability: the
working tunnel needed `--edge-ip-version 4` and `--protocol http2`, because the phone's hotspot
allowed Cloudflare region2 IPv4 TCP 7844 while region1, IPv6 and QUIC were unavailable. A temporary
tunnel that worked once on one network is a capability result, not a deployment. **No production
Cofferdam Action exists yet** — `create_task`, `get_result` and the rest are the M2I.5 milestone,
not shipped behaviour.

**The bounded Action vocabulary**, recorded now so the first implementation cannot quietly widen
it: `list_projects`, `get_project_context`, `create_task`, `get_task`, `get_updates`,
`get_pending_questions`, `submit_clarification_answer`, `send_followup`, `cancel_task`,
`get_result`.

**What the Custom GPT never receives**, by construction rather than by validation — there is no
request field for any of it, following D-2026-08-06-2: arbitrary shell, a path, a working
directory, an executable, raw CLI flags, an environment, credentials or secrets of any kind,
registry write access, `bypassPermissions` or any equivalent, arbitrary process control, and any
authority to approve a risky tool call or operating-system action.

## D-2026-08-08-2 — Cofferdam is the authority; every client is a view (EFE DECISION, ACTIVE)

**Decision.** Cofferdam remains the single authority for registered projects, workspaces, task
lifecycle, process lifecycle, typed workstation actions, model and profile allowlists, approvals,
audit and evidence, task questions and answers, results, and future project-memory access. ChatGPT,
the PWA, a future desktop companion, a future local assistant and an optional OpenClaw client are
all **clients of scoped APIs**. This restates D-2026-08-04-3(1) for the case where the client is
somebody else's product and is worth stating explicitly for that reason.

- **Task Core stays provider-neutral and model-free** (D-2026-08-06-2). Nothing about ChatGPT,
  Claude, Codex or any provider may enter it; the Actions bridge is a separate process in front of
  the task API, not a widening of it.
- **A remote client cannot mint an approval.** Risky tool calls and operating-system actions are
  approved on a Cofferdam human surface — the PWA today, the workstation itself — never by an
  Action, and never by text a model produced. The Actions surface therefore has **no approval
  endpoint at all**, which is a stronger statement than an endpoint that refuses.
- **The bridge is not the daemon.** The general Cofferdam API and the PWA are not exposed through
  whatever transport the Custom GPT reaches; the bridge publishes the Action vocabulary above and
  nothing else, under its own scoped per-client credential.

## D-2026-08-08-3 — The Claude architecture is dual-lane (EFE DECISION, ACTIVE)

**Decision.** Claude is reached through **two lanes that do not merge**, because they answer two
different questions and merging them would mean one of the two lying about the other.

- **Lane A — supervised native interactive sessions.** Claude Remote Control, per project, hosted
  and supervised by Cofferdam as user services. Cofferdam owns the **lifecycle**: start, stop,
  health, authentication expiry, restart, and a truthful failure state. It does **not** own the
  conversation. Transcript scraping, prompt injection into a native session, and mirroring session
  content into Cofferdam are out of the architecture — not deferred, excluded. What Cofferdam
  publishes about Lane A is a link and a state.
- **Lane B — delegated tasks.** Task Core plus official SDK/protocol adapters: the Claude Agent SDK
  first, a Codex app-server adapter later. Lane B owns structured questions, answers, provenance,
  activity, cancellation and durable results, and it is the only lane an Action can create work in.

The merged Claude Code CLI adapter (M2G) is Lane B's first implementation. It is retired only after
the Agent SDK adapter reaches verified parity on the behaviours PR #21 validated live — not on the
day the replacement first works.

## D-2026-08-08-4 — Custom GPT communication is user-turn-driven (RECORDED, ACTIVE)

**Cofferdam cannot push an unsolicited message into an inactive consumer ChatGPT conversation.**
That is a property of the product on the other side, and the architecture is built on it rather
than around it.

So: Cofferdam **stores** task state, questions and results, and the Custom GPT **retrieves** them
on a user turn through `get_task`, `get_updates`, `get_pending_questions` and `get_result`. Live
monitoring — a task that is running right now, watched without asking — is the Project
Workstation's job, not ChatGPT's. Turkish shortcuts such as `/durum`, `/soru`, `/sonuc` and
`/devam` may later exist as **Custom GPT instruction-level shortcuts** over those same Actions;
they are phrasing, not new authority.

Browser scraping of ChatGPT, cookie manipulation, and UI automation of the ChatGPT client are not
part of the architecture, in line with D-2026-08-04-7.

## D-2026-08-08-5 — OpenClaw stays optional, and may never become an authority (EFE DECISION, ACTIVE)

Extends D-2026-08-01-3 now that the client architecture is settled. OpenClaw is **not required**
for the Custom GPT loop, the Claude lanes, or the Project Workstation, and nothing in the plan
above waits on it. It may later be adopted as a Telegram/WebChat client, a notification channel, a
quick-response client, or a local-assistant experiment.

It must never become task authority, process authority, project-path authority, an arbitrary shell
gateway, or a required dependency of Cofferdam. The pre-M4 evaluation spike in
[`ROADMAP.md`](ROADMAP.md) is **superseded** by this entry: the delegated-task lane is being built
natively, so there is no longer a decision waiting on that spike.

## D-2026-08-08-6 — Memory is human-readable and user-owned (EFE DECISION, ACTIVE)

**Project memory is the repository's own Markdown** — `README.md`, `STATUS.md`, `DECISIONS.md`,
`ROADMAP.md`, and optionally a `memory/` directory — readable and editable by a person with no
Cofferdam running. **Personal memory** lives in a user-owned Markdown vault, Obsidian-compatible,
kept outside Cofferdam's home directory; Cofferdam reads it under an explicit grant and does not
own it.

**Task Core's SQLite database is runtime authority** for tasks and events (D-2026-08-06-2) and is
not memory. Any full-text index or embedding built later is a **derived index**: rebuildable,
discardable, and never the canonical copy of anything. If the index and the Markdown disagree, the
Markdown is right.

## D-2026-08-09-1 — The bridge's Action set: eight, not the recorded ten (EFE DECISION, ACTIVE)

**Decision.** M2I.5 PR1 ships eight Actions — `list_projects`, `create_task`, `list_recent_tasks`,
`sync_task`, `submit_choice_answer`, `send_followup`, `cancel_task`, `finish_task` — rather than
the ten names recorded in D-2026-08-08-1. That decision exists so the first implementation could
not quietly *widen* the surface, and this one records what happened instead: it narrowed, in one
place, with two small additions elsewhere.

**Four reads became one.** `get_task`, `get_updates`, `get_pending_questions` and `get_result` are
`sync_task`, returning a single bounded snapshot. A model that has to make four calls to answer
"what happened" will make three and guess the fourth, and the guess will be the result. One
snapshot also publishes strictly less than four separate responses would: the clarification read
happens only when the task says it is waiting on one, and the result read only when there could be
a result.

**`get_project_context` is not here.** The roadmap already places project-context retrieval in M2J,
and it cannot be built honestly before the workspace model it would read from exists. Omitted, not
dropped.

**Two additions, both bounded.** `list_recent_tasks` returns a strictly bounded, deterministically
ordered list with no task content, and exists so a conversation that has lost its task reference
recovers a real id instead of a model reconstructing one — the failure it prevents is an answer or
a cancellation aimed at the wrong task. `finish_task` is an existing Task Core lifecycle operation
and the honest alternative to `cancel_task`; without it, the only way to leave a task whose work
succeeded is to record it as stopped.

**What did not change.** No approval Action, no path, no provider settings, no Remote Control, no
transcript, no artifact browsing. The exclusions in D-2026-08-08-1 and D-2026-08-08-2 stand
unaltered.

## D-2026-08-09-2 — The bridge gets its own internal credential (EFE DECISION, ACTIVE)

**Decision.** The Actions bridge authenticates to the Cofferdam daemon with a **second 0600
credential**, separate from the device token, recognised on **ten task routes and nothing else**,
and generated only when the host explicitly enables it.

**The reason is provenance before access.** The daemon's task routes assign `origin` and `source`
from the authenticated caller. Had the bridge reused the device token, every bridge-created task
would have been recorded as though somebody had used their phone — which is precisely the mislabel
`tasks/clarifications.py` was written to prevent, and it would have been undetectable afterwards.

**The access property is structural, not a check.** The daemon's other routes keep the unchanged
`require_token`, which has never heard of the second credential. A bridge request to
`/api/remote-control/...` is therefore a 401 because nothing there can recognise it — a stronger
guarantee than a refusal a later refactor could relax, and the same reasoning D-2026-08-08-2 uses
for the absent approval endpoint.

**Consequence for the reserved provenance words.** `chatgpt_app` and `future_gpt_bridge` were
reserved in M2I PR2 and deliberately excluded from the accepted sets, on the rule that "a reserved
word is not an enabled surface". A surface now exists, so they are accepted — and the stored value
keeps the word `future` on purpose: it is in durable answer provenance on disk, and renaming it
would either rewrite history or split one source across two spellings.

**It is off by default and revocable by deletion.** No existing deployment gains a second
credential, and removing the file ends the bridge's access to the daemon while the phone keeps
working.

## D-2026-08-09-3 — Artifacts stay unavailable until Task Core owns them (EFE DECISION, ACTIVE)

**Decision.** The Actions bridge reports `artifacts_supported: false` with a reason, and exposes no
file listing, no preview and **no path parameter of any kind**.

**Cofferdam has no task-owned artifact model, and the thing that looks like one is not.**
`EvidenceReference` with `evidence_type: "artifact"` is an *adapter claim* carrying a free-form
identifier, documented in its own class as "never dereferenced by Task Core and never trusted as
fact". There is no manifest, no changed-file set, no digest, no project-root-relative path claim
and no ownership proof.

Building artifact Actions on that would mean inventing the ownership proof at the bridge — a remote
caller naming something and the bridge deciding whether the task owns it. That is the "add a path
parameter" mistake in a different coat.

**Absent rather than empty.** "No artifacts for this task" would be a claim the host cannot make;
"the capability does not exist" is the true one, and the payload says which.

**What has to come first**, as a Task Core PR, before any bridge artifact Action: structured
project-root-relative change claims from an adapter, bounded in count and length; storage against
the task with digest and size, marked `adapter_reported`; containment verified at record time under
the rule `projects.verify_root` already applies; a code-owned secret-path deny list applied at
record time rather than on read; and a bounded preview addressed by a server-minted `artifact_id`
with a size cap and a type allowlist. Detail in `docs/ACTIONS_BRIDGE.md`.

## The 2026-08-11 replan

Twelve entries recorded together on 2026-08-11, after M2I.5 closed, reshaping the work that
follows it around three things the product does not have: a workspace it can name, evidence it
can trust, and a local model that plans rather than implements. The planning package that
produced them is preserved as history in `handoffs/replan-2026-08-11/`; **these entries and
[`ROADMAP.md`](ROADMAP.md) are the authority**, and where the handoff disagrees with them the
handoff is a draft that was edited before adoption.

Nothing in this group is implemented. They are recorded before the work rather than after it
because each one constrains a component that does not exist yet, and the constraint is the part
worth writing down while it is still free to state.

## D-2026-08-11-1 — The sequence after M2I.5 is M2J → M2K → M2L → M2M (EFE DECISION, ACTIVE)

**Decision.** M2J is **preserved and reshaped**, and three milestones are named after it:

- **M2J — workspace, Working Context, mind foundation, Context Builder.** The recorded M2J scope
  (workspaces, project templates, a code-owned model allowlist, Auto/Safe/Review profiles,
  `get_project_context`, handoff and history surfaces) is kept and gains the durable
  "what are we working on" state the rest of this group reads from.
- **M2K — evidence and evaluation foundation**, model-free.
- **M2L — the first Local Planner milestone.**
- **M2M — remote operations and dashboard completion.**

**M2K comes before the planner, and that is the one ordering worth arguing.** The faster route is
a planner first — it demos sooner. It would also evaluate worker claims it has no means to check,
and it would be benchmarked on invented fixtures. The evidence bundle is worth building on its own
terms: it is deterministic, it needs no model, and the PWA and the Custom GPT can render
expected-vs-observed the day it exists. A planner arriving in M2L therefore arrives with factual
reason codes to explain rather than operational causes to invent.

**This is not a new bet; it is the repository's own recorded reasoning.** D-2026-08-09-1 left
`get_project_context` out of the Actions bridge because it "cannot be built honestly before the
workspace model it reads from." That sentence applies verbatim to a local planner, which is why
the foundation milestones come first.

M2J's profile work stays scoped to **evaluation depth and confirmation defaults** and never to
widening what a task may do — the warning already recorded against that milestone, unchanged.

## D-2026-08-11-2 — The Local Planner is an advisory coordinator, not a coding worker (EFE DECISION, ACTIVE)

**Decision.** The local model is a **planner, prompter, evaluator, coordinator and conversational
interface**. It understands messy Turkish and English input, holds the planning conversation,
drafts worker prompts and follow-ups, reads evidence, explains what happened, and recommends a
next step. **It writes no code, runs nothing, and holds no credentials.**

**Implementation stays delegated to cloud workers through Task Core**, and the worker abstraction
is the one that already exists: `TaskAdapter` + declared capabilities + `delegated_adapter`
(D-2026-08-06-2, and PR #34's rule that ordering is never authority). Codex, when it arrives, is a
third adapter — not a new "Worker" superclass, and not a new layer above the one that works.

**Planner output is advisory and never authority.** Its entire output surface is conversation text
plus schema-validated **proposals**, and a proposal becomes real only through an existing
validated path — `POST /api/tasks`, a clarification answer, a typed action — each with the same
validation and idempotency it has today. The planner never bypasses those paths and never gains a
shorter one. This is D-2026-08-08-2 restated for a client that happens to run on the same host.

**Placement:** a domain component of the workstation daemon (`cofferdam/workstation/planner/`),
speaking to a **separate model-runtime process** on loopback through a replaceable provider
client. It is **not** a `TaskAdapter` — adapters execute delegated work under the task lifecycle,
and a conversation is not a task — and it does **not** reach Task Core through the Actions bridge;
it calls the same internal service functions the HTTP routes use. Task Core stays provider-neutral
and model-free.

**Confirmation is explicit by default in M2L.** Every consequential planner proposal requires the
user to confirm it. No autonomous planner → worker continuation exists in the first planner
milestone. A per-workspace relaxation for low-risk sandbox projects is a possible *later* policy
decision and deliberately not a launch default: deciding now that it is out of M2L is what keeps
the milestone honest.

## D-2026-08-11-3 — Three minds: global vault, project repository, Working Context (EFE DECISION, ACTIVE)

**Decision.** Extends D-2026-08-08-6, which already binds: Markdown is canonical, derived indexes
are rebuildable and discardable, and where an index and the Markdown disagree the Markdown is
right. Three layers, three homes:

- **Global mind — a fresh, dedicated, Cofferdam-specific Obsidian-compatible vault**, user-owned
  and living outside `$COFFERDAM_HOME`, read under an explicit host-owned grant with the same
  validation posture as project roots. **The architecture is not bound to an absolute path**: the
  host chooses where the vault lives, and the grant names it. Likely durable content is user-level
  and cross-project — `USER.md`, `COMMUNICATION_STYLE.md`, `PREFERENCES.md`, freeform notes.
- **Project mind — the project's own repository**, canonical for its own memory. For **Cofferdam
  itself the existing documents are role-mapped** — `STATUS.md`, `ROADMAP.md`, `DECISIONS.md`,
  `DESIGN.md` — and **no `PLAN.md` is added merely to satisfy a filename convention**. A project
  with no established vocabulary may use a compact template (`PROJECT.md`, `RESEARCH.md`,
  `PLAN.md`, `DECISIONS.md`, `STATUS.md`). Workspace config records which file plays which
  **role**, so the Context Builder reads roles rather than hardcoded filenames.
- **Working Context — Cofferdam state, not Markdown.** Active workspace, current objective, active
  task reference, delegated worker, plan checkpoint, pending decisions, latest evidence reference,
  expected next step. It lives in SQLite under `state/`, the same posture as Task Core's store.

**Project STATUS/ROADMAP/DECISIONS are never duplicated into the global vault**, and no second
authority is created in either direction: no JSON mirror of Markdown, no Markdown mirror of
SQLite. A generated read-only `WORKING.md` export may exist for vault users, marked generated and
never read back as authority.

Obsidian compatibility means plain CommonMark, `[[wikilinks]]` and optional YAML frontmatter, and
nothing else. Cofferdam never invokes Obsidian, never reads `.obsidian/`, and never writes its
config; the vault works in a text editor with Cofferdam stopped. Wikilink resolution follows the
M2A alias rule — case-folded with the Turkish dotted/dotless-İ tailoring, ambiguity reported
rather than guessed.

## D-2026-08-11-4 — Memory writes are proposal → user accept → hash-bound apply (EFE DECISION, ACTIVE)

**Decision.** No model, local or cloud, ever silently writes durable memory. The planner
**proposes**, a person **accepts**, and Cofferdam **applies** — the posture Trust Core froze
(fail-closed, hash-bound, single-use) without importing its machinery yet.

1. A memory edit exists only as a **MemoryProposal**: target file inside the granted vault or
   project memory, **base content hash**, the change, a one-line why, and provenance.
2. Proposals are queued and visible; nothing touches disk before acceptance.
3. **Acceptance is a device-token surface** — the PWA or the workstation. The planner and the
   Actions bridge have **no acceptance route at all**: absent, not refusing, the same stronger
   statement D-2026-08-08-2 makes about approvals.
4. Apply is atomic and **refuses when the base hash no longer matches**. A drifted file means the
   human re-reads it rather than a stale diff landing on top of an edit they made.
5. Every applied proposal is recorded, bounded, so "why does my `USER.md` say this" has an answer.
6. **Deletion of durable memory is never planner-proposable.** Only the user deletes.
7. Working Context writes are exempt: they are state with dedicated validated routes, not
   canonical memory.

Indexes and any future vector store stay derived, rebuildable and discardable (D-2026-08-08-6).
Routing high-impact memory changes through the preserved Trust Core mint is recorded as the
insertion point and is not built now.

## D-2026-08-11-5 — Local context and external context are two security objects (EFE DECISION, ACTIVE)

**Decision.** A context pack assembled for the **local** planner and a context pack **leaving the
host** are not the same object and must not share one type. Repository-consistent names:
**`LocalContextPack`** and **`CloudContextProjection`**.

The Local Planner may receive rich local context — granted global mind, project mind, Working
Context, task state, evidence, user preferences — because it runs on the authority itself and its
provider client speaks only to loopback.

**Anything leaving the host passes through an explicit egress projection**, whatever the
destination: a cloud worker, the private Custom GPT, a ChatGPT browser skill, or any other
external model. Default posture:

| Included when relevant | Excluded by default |
|---|---|
| project plan and context | global personal memory |
| relevant project decisions | unrelated-project memory |
| current objective | vault paths and project filesystem roots |
| acceptance criteria | credentials and secrets (structurally absent) |
| selected project research | unrelated private notes |

Workspace policy may **later** explicitly allow selected global-mind extracts. It is an opt-in
naming what may leave, never an inference from a profile.

**The reason this is a decision rather than an implementation detail:** one universal
`ContextPack` would make every future caller a potential egress path, and the mistake would be
invisible — a field added for the planner would reach the Custom GPT the same day. Two types make
the boundary a compile-time question instead of a review question.

## D-2026-08-11-6 — Worker claims are not observations, and a model may not upgrade evidence (EFE DECISION, ACTIVE)

**Decision.** Extends the rule Task Core already enforces for single events — adapter-reported
evidence is a claim, not an observation — from one event to a whole turn, and adds the consumer
Task Core deliberately never had.

- **An `EvidenceBundle` carries every field with its source kind**: what the worker *claimed*,
  what Cofferdam *observed* (its own git observations, its own executed checks), and what nobody
  observed. Absent observation is **`unknown`, rendered unverified** — never inferred, and never
  reported as "did not happen". This is the M4 resource-audit honesty rule, unchanged.
- **Claims that contradict observations are both kept and flagged** (`claim_conflict`) rather than
  reconciled. The conflict is the evaluator's best input, and discarding either side destroys it.
- **Deterministic checks run before any model evaluation.**
- **The model layer may only downgrade, never upgrade.** A criterion the deterministic layer marked
  `failed` or `unverified` cannot become `verified` by model opinion. This is M6's
  "an advisory model review can never gate activation", inverted for verdicts.
- **Worker-reported success does not override missing evidence.** Expected A, B, C against observed
  A verified, B verified, C unverified is a `PARTIAL`, whatever the worker's final message says.
- **Risk level is code-owned or policy-derived, never self-selected by a model.** `LOW` is a
  deterministic pass plus a short sanity check; `MEDIUM` (the default) is the full bundle,
  expected-vs-observed, missing proof and risks; `HIGH/DEEP` adds prior decisions, sensitive paths,
  security/deployment/database implications, plan drift, regression and adversarial review. A model
  judgment may raise attention; it is never the security boundary, and it never grants.

A second, deeper model may later be routed for `HIGH` cases. **No second model is required for
M2L**, and until one exists a `HIGH` review runs on the same model with a larger budget and a
stricter prompt rather than blocking the milestone.

## D-2026-08-11-7 — Executable check text never comes from a request (EFE DECISION, ACTIVE)

**Decision.** Evidence checks are useful only if Cofferdam ran them itself, which makes "who
decides what runs" the whole security question. The answer is exactly two sources, and a request
is neither:

- **code-owned named checks**, or
- **host/operator-owned validated check definitions**, referenced by **stable ids**.

**The planner, the worker, a remote caller and a task prompt never provide executable command
text.** A workspace or task policy may *select* a pre-approved check id; it may not introduce new
executable text through a request. Execution is literal `argv`, **no shell**, a validated project
`cwd`, a bounded timeout and bounded output.

**This entry exists because the planning material was ambiguous and the ambiguity was the
dangerous kind.** It described check commands as "code-owned" while also placing an allowlist in
workspace configuration — two different authorities under one reassuring word. Configuration
written by the host operator is a legitimate second source; a command arriving inside a request is
not; and a document that calls both "code-owned" would eventually be implemented as whichever the
reader assumed. The registry rule this follows is already established: a registry selects among
capabilities the code has and can never introduce one (D-2026-08-04-5, D-2026-08-04-6).

## D-2026-08-11-8 — Health truth is evidence-led, and consequential work is never auto-retried (EFE DECISION, ACTIVE)

**Decision.** The machine records evidence first, a deterministic layer diagnoses second, and a
model phrases third.

A closed, code-extensible vocabulary of machine-observed reason codes — `NETWORK_UNREACHABLE`,
`DNS_FAILURE`, `PROVIDER_UNREACHABLE`, `PROVIDER_RATE_LIMIT`, `PROVIDER_AUTH`, `PROVIDER_QUOTA`,
`WORKER_EXITED`, `WORKER_TIMEOUT`, `WORKER_INTERRUPTED`, `HELPER_CRASH`, `LOCAL_SERVICE_RESTART`,
`HOST_SHUTDOWN`, `BROWSER_BRIDGE_DISCONNECTED`, `MODEL_RUNTIME_UNAVAILABLE`, `APPROVAL_REQUIRED`,
`USER_CLARIFICATION_REQUIRED`, `TUNNEL_DISCONNECTED`, `UNKNOWN` — **begins in M2K**, attached to
task failures where the adapter boundary can classify a real error. The consolidated overview and
dashboard complete in M2M.

**A diagnosis states its confidence: `observed`, `likely`, or `unknown`.** The planner may phrase
it naturally; it may not invent operational truth. Given a failed connectivity probe, a
disconnected provider worker and a healthy workstation, *"the worker likely stopped because this
host lost connectivity"* is allowed and *"your internet went down"* is not, unless that was
actually observed. When evidence is insufficient the honest sentence is the one rendered:
Cofferdam could not determine the cause.

**Retry policy.** Automatic retry covers idempotent reads and infrastructure reconnects already
under supervision. **Consequential operations are never automatically retried** — task creation,
follow-ups, clarification answers, actuator sends, memory applies. Those are user-triggered and
carry idempotency keys so that a retry after uncertainty is safe rather than duplicated. Host
reboot and service restart preserve durable task and history state and mark interrupted work
truthfully where continuation is unavailable, which is the behaviour restart reconciliation
already implements.

## D-2026-08-11-9 — Browser automation is a provider-neutral actuator track (EFE DECISION, ACTIVE)

**Decision.** `BrowserActuator` is a Cofferdam-owned interface with narrow semantic verbs; the
implementation is a configured provider behind it, exactly like Task Core's adapters. **No winner
is chosen in advance.** The isolated evaluation compares three candidates against one checklist:

1. **Playwright** against a dedicated real Chrome/Chromium profile,
2. **Kimi WebBridge**, if its Ubuntu local-agent path can be made operational and driven
   semantically,
3. **BrowserSkill**, on the same acceptance checklist.

All viable providers run the same narrow spike: a known logged-in ChatGPT conversation → a
nonce-tagged exact prompt → submit → wait for truthful completion → extract only the final
assistant response → stop.

**D-2026-08-04-7 is unchanged and decides admissibility.** Semantic automation only — DOM,
accessibility or extension APIs. **A provider that operates by screenshots and coordinates is out
regardless of its other merits.**

- **A dedicated automation profile**, not the user's daily browser profile. A logged-in browser is
  a high-value credential, and the daily profile would make every signed-in site reachable by a
  buggy or compromised skill. The profile directory is treated as a secret.
- **Skills, not scripts.** `chatgpt.ask` and its kind are code-owned definitions with site
  knowledge and completion rules, versioned and reviewed. The planner selects a skill id and
  bounded arguments; it never emits selectors, JavaScript or steps, and a skill never arrives from
  a request.
- **User-triggered and single-shot.** No autonomous ChatGPT ↔ worker loop. Sending content to an
  external service is confirm-by-default.
- **Anything read from a page is data with provenance `external_model_output`**, never
  instructions — never executed, never auto-triggering an action.
- Remote surfaces see actuator status and bounded results only; **no raw browser-control surface is
  exposed anywhere**.

Driving a consumer web UI by automation sits in a gray zone of the provider's terms; the design
keeps volume low and isolates it so that removing it removes one unit and one skill. The Custom GPT
Actions path remains the sanctioned integration.

## D-2026-08-11-10 — Ollama is the initial runtime; the model is a benchmark result (EFE DECISION, ACTIVE)

**Decision.** **Ollama** is the initial recommended local-model runtime, as its own systemd user
unit on loopback, **behind a replaceable provider boundary** — one file speaks HTTP to it, and
planner logic never imports a model SDK. llama.cpp server remains a drop-in alternative behind the
same client. The Ollama posture already recorded stands: optional, and the daemon runs fine
without it (`MODEL_RUNTIME_UNAVAILABLE` is a first-class state; planner routes answer
`planner_unavailable` and nothing else is affected).

**Qwen3.5-9B quantized is the current benchmark candidate, not an architectural dependency**, and
**no exact model or tag is frozen into durable architecture** until Track D has verified the
actual host, runtime and model combination. Configuration maps **roles → provider + model** against
a code-owned model allowlist — a config selects among allowed models and can never introduce one.
`planner.deep` and `planner.fast` are reserved role words from day one; the MVP ships one role and
one model.

**The model is chosen by measurement, not intuition.** Track D scores candidates on the work
Cofferdam actually does: messy Turkish intent, project/context understanding, plan extraction,
worker-prompt quality, follow-up quality, result explanation, expected-vs-observed evaluation,
unsupported-claim detection, tool selection, deciding **not** to act, and asking for
clarification. A failure that a better context pack fixes is a Context Builder bug, not a reason
to buy a bigger model.

**Real and sanitized private fixtures stay local-only initially.** Real Turkish phrasing and
private project examples remain off the public repository; committed fixtures are synthetic or
public-safe. Promoting selected real fixtures is a later, explicit review decision.

## D-2026-08-11-11 — The surfaces do not change (EFE DECISION, ACTIVE)

**Decision.** The workstation is intended to work as a personal private server — the user leaves
the machine at home and reaches it from a phone, a browser, another computer or a future native
app. **The host remains the authority and every remote device remains a control surface.**
Execution, memory, credentials, workers, browser automation and local models stay on the host
wherever practical.

Nothing in this replan widens exposure. Restated because new components are the usual way a
boundary erodes:

- **The main API and the PWA stay tailnet-private.** The remote dashboard *is* the PWA, reached
  over Tailscale. It is not published through the existing tunnel, and no second public origin is
  added for it. A future dedicated app is a new client of the same tailnet API, gaining nothing the
  PWA does not have.
- **The private Custom GPT stays a bounded conversation surface** through the Actions bridge,
  poll-only (D-2026-08-08-4), and remains the only public surface.
- **The PWA (device token, tailnet) is the decision surface** — and now more so, since memory
  acceptance lands on it. That is precisely why memory acceptance is never added to the bridge.
- **No generic public shell, filesystem API or browser-control surface**, no provider session ids,
  no raw hidden reasoning, no adapter/model/provider selection by an external caller.

**Normal use should not require internal identifiers.** "Claude ne yaptı?", "Bitti mi?", "İkinci
seçeneği seç", "İnternet mi gitti, niye durmuş?" are the target interaction; task ids, provider
session ids and adapter ids stay internal, and commands remain power-user shortcuts rather than
the price of entry.

**The default loop stays human-directed**: the user discusses, the planner drafts, the user
confirms, the worker implements, the planner evaluates, the user decides. Autonomous
planner → worker → planner loops are **not** recorded as the roadmap's direction. They would need
their own explicit decision.

## D-2026-08-11-12 — One supervised validation-debt pass before M2J PR1 merges (EFE DECISION, SUPERSEDED IN PART BY D-2026-08-12-1)

> **Status note, 2026-08-12.** The supervised pass ran. Its *blocking scope* is narrowed by
> [D-2026-08-12-1](#d-2026-08-12-1--validation-debt-is-classified-by-blast-radius-efe-decision-active):
> the core lifecycle, authority and deployment items below were cleared and are now PASS; the
> remaining media-feature walkthroughs are reclassified as deferred, non-blocking debt. The
> original text stands unedited as history — it was correct when written, and the items it names
> were not silently absorbed.

**Decision.** The inherited unclosed live validations — the M2B logout/login cycle, the M2C write
path, M2D/M2D.1 and M2E re-validation, and the unconfigured media provider credentials — get **one
supervised cleanup pass before M2J PR1 is merged**.

It does **not** block starting M2J PR1 development or preparation. It does block the merge: PR1
must not land while that agreed debt is unresolved without a new explicit decision.

**The reason is habit rather than tidiness.** Every milestone in this replan is validated live on
the real host by design, and the debt is a record of that habit slipping. Clearing it costs little
and stops STATUS carrying caveats that readers learn to skip.

## D-2026-08-12-1 — Validation debt is classified by blast radius (EFE DECISION, ACTIVE)

**Decision.** [D-2026-08-11-12](#d-2026-08-11-12--one-supervised-validation-debt-pass-before-m2j-pr1-merges-efe-decision-superseded-in-part-by-d-2026-08-12-1)
treated all inherited live-validation debt as one undifferentiated blocker. It is now split by
what the debt actually threatens:

- **Blocking.** Unresolved validation debt that threatens the **core architecture, lifecycle,
  authority, or deployment boundaries** blocks M2J PR1. These are the properties every later
  milestone builds on, and a wrong assumption there is not recoverable by a later fix.
- **Non-blocking.** Peripheral **media-feature** live-validation debt does **not** block
  workspace/mind/planner development, and is tracked separately in
  [`STATUS.md`](STATUS.md#inherited-live-validation-debt).

**This is a priority decision, not a validation result.** Nothing here asserts that a deferred
item passed. The supervised pass of 2026-08-11 closed the blocking set on the real host:

- the real GNOME logout → GDM → login cycle, 475 bounded read-only samples across 40 minutes;
- workstation survival under the systemd **user** manager, unchanged `MainPID`, `NRestarts=0`;
- truthful pre-login/post-login capability transitions, with zero mismatches against the real
  session;
- no graphical-session ownership confusion — GDM's own greeter session was present while logged
  out and was correctly **not** claimed;
- the authenticated/unauthenticated boundary, 401 in every sample;
- production integrity across the cycle, and **zero** task or provider mutation during it;
- the M2C bounded audio write path, including refusal behaviour for invalid writes;
- production A/B deployment integrity, with **no** stale validation drop-ins present.

**What stays open is named, not erased.** The remaining media walkthroughs — Spotify transport,
queue, volume and device transfer, and the M2E YouTube player run — keep an explicit state in
STATUS rather than dissolving into prose. The vocabulary is fixed so a later reader cannot
mistake one for another: `PASS`, `DEFERRED_NON_BLOCKING`, `BLOCKED_BY_PREREQUISITE`,
`DOCUMENTATION_STALE`.

**Do not restart the media validation loop on the strength of stale text.** Two things repeatedly
sent sessions back into it. First, the `90-`/`95-` validation drop-in instructions in the M2D and
M2E checklists describe a pre-merge runtime that pointed the live service at unmerged feature
clones; production now runs the merged A/B slot deployment, and re-applying them would recreate
exactly the production drift M2H PR4 removed and `test_deployment_drift.py` guards. Second, STATUS
described the media provider credentials as unconfigured; they are configured and functional.
Both are corrected in this change.

**Device transfer stays honestly blocked.** Only one Spotify Connect device exists on this host,
so transfer is `BLOCKED_BY_PREREQUISITE` — a missing prerequisite, never a pass.

## D-2026-08-12-2 — The vault grant must say yes, and PR2 only edits documents that exist (EFE DECISION, ACTIVE)

**Decision.** Two operator answers to questions M2J PR2 raised while it was on its branch. Both
narrow [D-2026-08-11-4](#d-2026-08-11-4--memory-writes-are-proposal--user-accept--hash-bound-apply-efe-decision-active);
neither changes it.

**A. Global Mind access requires an explicit host-owned `"enabled": true`. The presence of the
grant file alone does not activate access.**

| State | Result |
|---|---|
| `config/mind-grant.json` absent | inaccessible |
| present, `enabled` omitted | inaccessible |
| `enabled: false` | inaccessible |
| `enabled` present but not a boolean | configuration error, fails closed |
| `enabled: true`, and otherwise valid | accessible |

This is **deliberately stricter than the project and workspace convention**, where `enabled`
defaults to `true` and omitting it means on. That convenience is correct for
`task-projects.json` and `workspaces.json`: they say where work happens on this machine, an
operator writes an entry in order to use it, and the cost of a mistake is a project that runs
when it was meant to be parked.

The vault is not that. It is **cross-project personal authority** — the one file on the host that
makes somebody's private, non-project memory readable at all — and the failure mode is not a
surprised operator but personal memory reachable by a component that was never meant to see it.
When the two conventions disagree, the one that fails closed wins, and the activating act is
made explicit rather than incidental.

The type check is `isinstance(value, bool)` rather than truthiness, because `1`, `"true"` and
`"yes"` are exactly what a person writes meaning yes and exactly what must not be read as
consent. The three written-but-inactive states are **reported** rather than silent, so an
operator can tell "I never granted one" from "I granted one and it is off".

The grant is re-read on every resolution rather than cached, so revocation takes effect
immediately and applies to a proposal that is already pending: acceptance re-resolves the grant
and refuses, writing nothing. The proposal stays **pending** rather than being decided — the
authority was withdrawn, not the change.

**B. M2J PR2 modifies existing approved documents only. Creating a missing one is out of scope.**

A role mapped to a file that does not exist fails closed, at proposal time and again at apply
time. PR2 adds no file creation, directory creation, rename, move or deletion, and the absence is
structural rather than a check: the operation vocabulary contains one word, and no function in
the mind package removes or creates a path.

**Creation of an approved-but-absent memory document requires its own future authority
decision**, and it is recorded as out of scope rather than left as an implementation gap. The
reason it is not a small extension: a grant that may create files is a grant over a *directory*,
not over the documents an operator named, and every containment argument in
[D-2026-08-11-3](#d-2026-08-11-3--three-minds-global-vault-project-repository-working-context-efe-decision-active)
is written about named documents.

## D-2026-08-12-3 — A memory apply is bound to authority, is crash-truthful, and resolves by descriptor (EFE DECISION, ACTIVE)

**Decision.** Three hardenings of the path
[D-2026-08-11-4](#d-2026-08-11-4--memory-writes-are-proposal--user-accept--hash-bound-apply-efe-decision-active)
already specifies, adopted after the focused M2J PR2 security review reported them as residual
risks. None widens what memory access can do; each closes a gap between what PR2 *claimed* and
what it enforced.

**A. A proposal is bound to the host authority, not only to the bytes.** The base content hash
answers "is this still the text I reviewed"; it cannot answer "is this still the same
*document*". Remap a role from one approved file to another holding byte-identical content and a
content-only check sees no drift at all. So a proposal also records an opaque, domain-separated
fingerprint of the authority that resolved it — scope, workspace, project, role, canonical root
and the configured relative name — recomputed at acceptance and compared. A mismatch is
`mind_target_authority_changed`, its own reason rather than a content conflict, because sending
somebody to look for an edit that never happened is a worse answer than no answer.

The fingerprint is **stored, never published, and never a path**: paths go in as bytes and come
out as a digest. This is the one place a filesystem location influences durable state, and it does
so one-way.

**B. The store may never durably say `applied` while the document still holds the pre-apply
bytes.** The first implementation committed `applied` and then wrote, which bought exclusivity —
two accepts cannot both pass one compare-and-set — at the cost of exactly that lie in the crash
window. The protocol is now `pending → applying → applied`: the claim is a statement of *intent*,
true whenever it is written and however the process ends, and completion is recorded only after
the rename returns. The claim remains a durable compare-and-set, so single-writer is unchanged.

**Recovery classifies; it never writes.** At start-up each outstanding claim is compared against
the document's own hash: equal to the proposed content means the bytes landed and only the record
was lost, and the record is reconciled; equal to the base means the mutation did not land, and the
proposal becomes `interrupted`, waiting for a person on the private surface; anything else is
conflicted and terminal. **A consequential operation resumed by a restart is one nobody authorized
at the moment it happened**, which is the same rule
[D-2026-08-11-8](#d-2026-08-11-8--health-truth-is-evidence-led-and-consequential-work-is-never-auto-retried-efe-decision-active)
applies to task creation and actuator sends.

**C. Containment is enforced by descriptor-relative traversal, not by a pathname walk.** Checking
a path with `lstat` and then opening it by name is two views of the filesystem with a usable gap
between them. Resolution now opens a descriptor on the verified root and opens every component
below it relative to the one above, `O_NOFOLLOW` throughout, and holds the parent descriptor for
the whole operation — so the hash that authorizes a write and the write itself concern one file,
and a directory swapped in afterwards redirects nothing. The temporary file and the rename are
both relative to that descriptor.

**There is no pathname fallback.** Where the platform lacks the primitives, resolution refuses
(`mind_resolution_unsupported`) rather than quietly reverting to the racy version: a weaker
guarantee that looks identical from the outside is worse than a refusal, because nothing
downstream would know it had been weakened. The supported production host is Ubuntu, where every
primitive is present.

**What did not change.** Roles stay closed vocabularies, requests still carry no path, deletion
and creation stay absent, acceptance stays a device-token surface, and the Actions bridge still
has no route to any of it.

## D-2026-08-12-4 — Semantic retrieval is a required Mind capability, and its index is never authority (EFE DECISION, ACTIVE)

**Decision.** Cofferdam Mind requires **two** relationship mechanisms over canonical Markdown, and
they are complementary rather than alternatives:

- **explicit links and backlinks** — intentional, human-readable, written by a person, and legible
  in a text editor with Cofferdam stopped;
- **semantic/vector retrieval** — which surfaces memory that is relevant but was never explicitly
  linked, and that does not share the words used to look for it.

**Semantic retrieval is a required capability, not an optional search enhancement.** Recorded here
because the roadmap previously implied otherwise: M2N sat under a "Later, unordered" heading beside
items explicitly marked optional or conditional, and nothing anywhere stated that the capability
was needed at all. Ordering was clear; necessity was not.

The intended product behaviour is specific. When a new idea arrives, Cofferdam should be able to
relate it to prior ideas, decisions and project context **even when they share no exact words and
no explicit link**, so the planner can surface the related material, a possible contradiction, a
prior decision the idea affects, and a suggested durable link worth writing down.

**Retrieval reads. It never writes.** Everything above is a *reading* capability whose output is
material for a person and, later, for the planner. Any resulting change to canonical Markdown goes
through the path
[D-2026-08-11-4](#d-2026-08-11-4--memory-writes-are-proposal--user-accept--hash-bound-apply-efe-decision-active)
and [D-2026-08-12-3](#d-2026-08-12-3--a-memory-apply-is-bound-to-authority-is-crash-truthful-and-resolves-by-descriptor-efe-decision-active)
already define — MemoryProposal, explicit private acceptance, hash- and authority-bound apply —
unchanged and with no exception for a suggestion that came from retrieval. Finding a link worth
writing is not permission to write it.

**The index is never a second memory authority.** Extending
[D-2026-08-08-6](#d-2026-08-08-6--memory-is-human-readable-and-user-owned-efe-decision-active),
any embedding, vector or full-text relationship index is:

- **derived** — built from canonical or approved source material, never authored;
- **rebuildable** from that material alone;
- **discardable** — deleting it must not delete or alter one byte of canonical memory, and must
  leave a host that reads memory exactly as it did before, only without the extra recall;
- **non-canonical** — where the index and the Markdown disagree, **the Markdown is right**;
- **provenance-preserving** — a retrieved fragment carries where it came from, so the planner can
  tell memory from observation from external text
  ([D-2026-08-11-5](#d-2026-08-11-5--local-context-and-external-context-are-two-security-objects-efe-decision-active));
- **local by default** — the index is built and queried on the host. This was the second thing the
  record never said: derived and rebuildable were binding, locality was not. A vector store is a
  lossy copy of somebody's private memory, and shipping one to a service by default would move
  personal memory off the machine through a component nobody thinks of as memory. Anything leaving
  the host is still a `CloudContextProjection` built by an explicit egress policy.

**Ordering is unchanged: backlinks first, vectors second** — the explicit graph is cheaper, exact,
and useful on its own, and it is the thing a person can read. **M2N remains a later milestone.** It
does not block M2J PR3, M2J PR4, M2K or M2L, and nothing in this decision authorizes implementing
embeddings, vectors, backlinks or retrieval now.

## D-2026-08-13-1 — The runtime home and the project root are different authorities (EFE DECISION, ACTIVE)

**Decision.** Two roots that were the same directory on this host are now separated, because they
answer different questions:

- **`COFFERDAM_HOME`** (`~/cofferdam`) is the **operational** authority: `state/`, `secrets/`,
  `config/`, `slots/`, `logs/`, `clones/`, `worktrees/`. It is where the product keeps its own
  running.
- **`project.root`** in `task-projects.json` is the **canonical source** authority: where a
  project's code and documents live, where a worker runs, and where Project Mind resolves a role.

For the `cofferdam` project itself, `project.root` becomes a **stable source checkout**
(`~/cofferdam/repo`) and **must never point at an A/B deployment slot**. A slot is not a project:
`slots/a` and `slots/b` swap on every deployment, so a role mapped into one would silently point
project memory at the previous release after the next normalization — and PR2's target-binding
hash would refuse every pending proposal the moment the slot flipped
([D-2026-08-12-3](#d-2026-08-12-3--a-memory-apply-is-bound-to-authority-is-crash-truthful-and-resolves-by-descriptor-efe-decision-active)).
Project-memory identity must not change when a deployment happens.

**No new path authority is introduced.** There is no `mind_root`, no `docs_root`, no
`memory_root`, no second project for Mind, and no symlink into a slot. The project registry stays
the single root authority and every consumer keeps reading it.

**The separation was already true in code; only this host's configuration conflated them.** A task
row stores `project_id` and never a path — the root is resolved live from the registry at every
create, follow-up, answer and cancel — so no history is invalidated by moving it. `Config` owns the
home; nothing writes runtime state under a project root.

**It also narrows a real exposure.** While `project.root` was `$COFFERDAM_HOME`, a worker in the
`cofferdam` project would have had `secrets/`, `state/tasks/`, `slots/` and the service environment
files inside its working directory. That was latent rather than active — the project permitted no
adapter and no Remote Control — but a project root is a grant, and it should name source rather
than the machine's own operational insides.

## D-2026-08-13-2 — Read authority is not context inclusion (EFE DECISION, ACTIVE)

**Decision.** Closes OQ-5. There are **three** separate permissions over a piece of memory, and
Cofferdam must never let one imply the next:

| | Question it answers | Decided by |
|---|---|---|
| **Read authority** | may Cofferdam open this at all? | the host-owned grant and the role map |
| **Context inclusion** | should this be in *this* pack? | context policy — **this decision** |
| **Egress permission** | may this leave the host? | `CloudContextProjection` and its egress policy ([D-2026-08-11-5](#d-2026-08-11-5--local-context-and-external-context-are-two-security-objects-efe-decision-active)) |

**Mind read authority ≠ automatic context inclusion. LocalContextPack inclusion ≠
CloudContextProjection permission.** Cloud egress remains an entirely separate policy and nothing
here touches it.

**The default automatically eligible Global Mind roles are `communication_style` and
`preferences`.** `user` and `cross_project` are **not automatically injected**, and that is
intentional rather than an unfinished edge:

- A `LocalContextPack` should carry context appropriate to the **current interaction**, not every
  piece of locally accessible memory.
- `USER.md` may eventually hold broad personal information irrelevant to most requests.
- `CROSS_PROJECT.md` is especially prone to context pollution when the active workspace concerns
  one project.
- That Cofferdam is *allowed* to read a role does not mean every planner request should receive
  it.

**How the excluded roles are meant to arrive later.** `user` through an explicit local
policy or reference when relevant; `cross_project` primarily through an explicit reference or
future M2N semantic retrieval, when another project's memory is **actually** related. Either way
the material enters as a typed candidate through the Context Builder's existing seam and is
subject to the same budget, provenance and omission machinery — retrieval never bypasses it.

**This authorizes nothing to be built now.** No M2N, no semantic relevance, and specifically **no
keyword heuristic** invented to guess when `user` or `cross_project` is relevant — the failure
[D-2026-08-12-4](#d-2026-08-12-4--semantic-retrieval-is-a-required-mind-capability-and-its-index-is-never-authority-efe-decision-active)
describes, where an approximation gets believed because it is labelled "relevant", applies exactly
here. Widening the default set is a change to this decision, with a test.

## D-2026-08-13-3 — `CloudContextProjection` is M2J PR3.5, and it is the gate on PR4 (EFE DECISION, ACTIVE)

**Decision.** Egress projection gets its **own milestone**, and no surface that crosses the host
boundary ships before it.

| Milestone | Owns |
|---|---|
| **M2J PR3** | `LocalContextPack` — rich local context. **Complete**, PR #41. |
| **M2J PR3.5** | `CloudContextProjection` and the egress policy. **This decision.** |
| **M2J PR4** | Workspace and project-context *surfaces* — the PWA panel and a read-only `get_project_context`. **Gated on PR3.5.** `syncWorkspace` is M2M's, per [D-2026-08-13-4](#d-2026-08-13-4--m2j-pr4-is-the-read-surface-syncworkspace-belongs-to-m2m-efe-decision-active). |

**The invariant PR4 is held to:**

> No external surface may return a `LocalContextPack`, or anything derived from one, except a
> `CloudContextProjection` produced by a named egress policy.

**Why this is recorded rather than assumed.** The deployment audit after PR3 found the record
saying three incompatible things at once:
[D-2026-08-11-5](#d-2026-08-11-5--local-context-and-external-context-are-two-security-objects-efe-decision-active)
required the second object, `STATUS.md` attributed it to PR3 in two places, PR3's own record
correctly said it was not built, and **no milestone owned it**. PR4 was scoped as "surfaces" with
nothing standing between it and a route returning local context. That is precisely the failure
mode D-2026-08-11-5 exists to prevent — a field added for the planner reaching the Custom GPT the
same day — arriving through a documentation gap rather than through a code change. A boundary that
lives only in a docstring is one somebody implements around without noticing.

**PR3 is not retroactively credited with projection.** It did not build one, said so, and was
right to. The correction is that a milestone now owns the work, not that the history changes.

**What PR3.5 is.** One narrow, code-owned, versioned profile — `project_context_external_v1` —
that is **deny-by-default**. Eligibility is decided on the decomposed semantic reference rather
than on `source_kind`, because `global:preferences` and `project:cofferdam:status` are both
`memory` and a policy keyed on the kind would publish personal memory the first time somebody
wrote the obvious condition.

- **Allowed:** bounded project `status`, `plan` and `decisions`, plus four allowlisted Working
  Context fields — objective, expected next step, and the plan-checkpoint and pending-decision
  references a person recorded by hand.
- **Denied by default:** all four Global Mind roles **including `communication_style` and
  `preferences`**, which are in every local pack on the production host; the current user message;
  `design`; every other project; the evaluation slot; and every scheme the profile cannot
  classify.
- **Content as well as metadata.** PR3's validation proved canonical Markdown legitimately
  contains `slots/a`, vault roots and operational paths — a semantic `source_ref` says nothing
  about the text beneath it. Recognised local paths are replaced and declared; credential-shaped
  material omits the **whole part** rather than being rewritten, because a clever lossy edit of a
  possible secret is a guess that is permanent when wrong.
- **Its own budget.** 16 KiB of UTF-8, not the local pack's 64 KiB. One number governing both
  "how much may the planner see" and "how much may leave the machine" is a number a later tuning
  change widens by accident.

**Pattern matching is not the boundary, and this decision does not claim it is.** No scanner can
prove arbitrary text contains no secret. The protection is the layering — a narrow source
allowlist, Global Mind excluded by default, a structured field allowlist, the semantic reference
grammar, known-host-value redaction, conservative secret detection, fail-closed omission and a
byte bound — of which only two steps are recognition and the rest are construction. The residual
limits are listed on the object itself and asserted as tests.

**Eligibility is not transport.** A `CloudContextProjection` performs no network activity and
holding one is not permission to send it. A later surface still needs its own authentication,
authorization, destination contract and user-consequence semantics. "Cloud" names what the object
is shaped for, not where it goes.

**This authorizes no surface, no provider and no retrieval.** PR3.5 adds no HTTP route, no Actions
bridge operation, no OpenAPI change and no model. A future M2N retrieval candidate admitted into a
pack is re-evaluated by this policy independently — admission to a pack has never implied
admission to a projection, and there is deliberately no candidate branch that could make it.
A future workspace policy explicitly allowing selected Global Mind extracts remains permitted by
[D-2026-08-11-5](#d-2026-08-11-5--local-context-and-external-context-are-two-security-objects-efe-decision-active)
and is **not built here**; today's default stays exclusion, and widening it is a change to this
decision, with a test.

## D-2026-08-13-4 — M2J PR4 is the read surface; `syncWorkspace` belongs to M2M (EFE DECISION, ACTIVE)

**Decision.** `get_project_context` is **M2J PR4**. `syncWorkspace` is **M2M — Remote operations
completion**. PR4 ships a read surface and no mutation of any kind.

| Operation | Milestone | Why |
|---|---|---|
| `get_project_context` | **M2J PR4** | A read that crosses the host boundary — exactly what [D-2026-08-13-3](#d-2026-08-13-3--cloudcontextprojection-is-m2j-pr35-and-it-is-the-gate-on-pr4-efe-decision-active)'s egress policy governs. |
| `syncWorkspace` | **M2M** | A mutation. Nothing in the egress policy authorizes it. |

**What PR4 owns:** the PWA workspace/context panel; a read-only `get_project_context`;
serialization of a `CloudContextProjection`; authentication; authorization; the destination
contract; and the user-visible read semantics.

**What PR4 must not contain:** `syncWorkspace`, workspace mutation, project mutation, memory
mutation, or any other remote state change.

**Why this is recorded.** The record double-booked it. `ROADMAP.md` listed `syncWorkspace` under
both M2J PR4 and M2M, and
[D-2026-08-13-3](#d-2026-08-13-3--cloudcontextprojection-is-m2j-pr35-and-it-is-the-gate-on-pr4-efe-decision-active)'s
own table repeated the PR4 half. Two milestones owning one Action is how an Action ships twice with
different semantics, or ships in the milestone whose review was scoped for the other question.

**Why the split falls here rather than by convenience.** PR3.5 answers exactly one question — *may
this leave the host?* A read surface is that question plus authentication and a destination. A
mutation is a different set: authority to change state, idempotency, conflict resolution when the
phone and the desktop disagree, and the consequential-action semantics that
[D-2026-08-11-8](#d-2026-08-11-8--health-truth-is-evidence-led-and-consequential-work-is-never-auto-retried-efe-decision-active)
attaches to work that has effects. Projection grants none of that and was never meant to.

Shipping both in PR4 would put two security boundaries behind one review, and the weaker argument
— "the projection already vetted this" — would be available for the half it does not cover. The
egress policy is not a mutation authority, and a milestone boundary is the cheapest place to keep
that true.

**This does not weaken the PR4 gate.** The invariant from D-2026-08-13-3 stands unchanged: no
external surface may return a `LocalContextPack`, or anything derived from one, except a
`CloudContextProjection` produced by a named egress policy. Narrowing PR4's scope narrows what has
to clear that gate; it does not lower it.

## D-2026-08-13-5 — A pack may be built without a message, and never with a fake one (EFE DECISION, ACTIVE)

**Decision.** `ContextBuilder` gains a second entry point, `build_without_message()`, which
assembles the ordinary pack **minus** its highest-priority part and records an omission saying so.
`build()` is unchanged, and `build(None)` still refuses.

**The alternative that was refused.** `get_project_context` has no user message — the Custom GPT
already has the conversation, and the PWA panel is not asking anything. The obvious shortcut was to
pass a synthetic marker so the existing signature was satisfied. That would have placed non-user
text in the pack as `source_kind=user_instruction`, `source_ref=user:current_message` — a part whose
kind and reference disagree with the truth, which is **precisely** the shape
[D-2026-08-13-3](#d-2026-08-13-3--cloudcontextprojection-is-m2j-pr35-and-it-is-the-gate-on-pr4-efe-decision-active)'s
projector refuses from a producer as `source_kind_mismatch`. A rule the builder breaks quietly is
worse than no rule, and provenance that is true except when it is inconvenient is not provenance.

**Why this is a narrowing and not a widening.** No source is added, no role becomes readable, no
bound is relaxed, and the method takes no argument that selects anything — it has no parameters
beyond the budget and candidate seams `build()` already had. The result is a strict subset of what
`build()` could produce. A caller cannot use it to reach material `build()` could not.

**`None` is not the spelling.** A private module sentinel marks "there is no message", so
`build(None)` remains `CurrentMessageInvalid`. A caller arriving at the message path with nothing in
hand is a bug, not a request for a message-free pack, and one value cannot mean both.

**What still holds.** `user:current_message` remains denied by `project_context_external_v1`
regardless — the pack simply no longer has one to deny. The omission row uses its own reason,
`no_current_message`, rather than borrowing `source_not_in_this_build`, because "this request had no
message" and "this build has no evaluator" are different facts.

## D-2026-08-14-1 — The evidence bundle is derived; turn bounds are the only thing persisted (EFE DECISION, ACTIVE)

**Decision.** `EvidenceBundle` is assembled **on read** from stored immutable facts and is never
persisted — no table, no serialized column, no schema version for one. Schema **v5** adds exactly
one additive table, `task_turn_bounds`, holding the event-sequence range each turn owns.

**Why the bundle is not stored.** Every input it needs is already durable and already immutable:
`task_change_claims`, `task_claim_ingestion`, the append-only `task_events.evidence_json`, and the
bounds below. Assembly re-runs nothing — no Git, no filesystem, no provider — so a bundle is a
*function* of rows that do not change, and storing the output of that function would create a
second source of truth that can drift from the first and has to be migrated every time the
assembler improves. A future `EvaluationRecord` that needs to name *this* evidence names it by
`(task_id, turn_number, assembler_version, input_fingerprint)`: four small values that identify a
snapshot without copying it.

**Why v5 exists anyway, and why it is only bounds.** The PR1 audit proved something that is easy to
assume away: **exact turn attribution cannot be reconstructed from v4 durable data.** A
`ChangeClaim` carries an exact `turn_number`. A `task_events` row carries an exact `sequence`.
`task_turns` carried neither end of the sequence range it owns, and the only v4 bridge between them
was a pair of timestamps.

**Timestamps are not an authoritative shared boundary**, in three separate ways. Two events can
share a millisecond. A clock can move backwards or be corrected between the write that opens a turn
and the write that appends an event. And `started_at` is produced by a different call than the one
that allocates the sequence, so even a perfect clock leaves a window where "later timestamp" and
"later sequence" disagree. Attribution built on any of that would be right most of the time, which
for evidence is worse than being absent — it would look exact.

So the boundary is **written, not inferred**, at the two moments Cofferdam already holds the
authoritative cursor: inside `_open_turn_locked` and `_close_turn_locked`, in the same SQLite
transaction as the turn lifecycle operation. There are exactly two turn-open call paths and one
close path, and all three funnel through those helpers, which is what makes "a v5 turn without a
bound" a state the code cannot produce rather than a state a test happens not to find.

**Historical turns are not backfilled.** Production's pre-v5 turns receive **no** inferred rows —
nothing from `started_at`, `completed_at`, event timestamps, event types, the nearest sequence or
the task's state. They report `turn_attribution = legacy_unknown` and receive **no machine
observations at all**. A legacy turn shown task-wide observations would be a turn-scoped claim
built from task-scoped evidence, which is the exact confusion v5 exists to end. A smaller true
answer beats a larger invented one.

**Rollback consequence, recorded rather than solved.** The forward-only schema gate is unchanged, so
a v4 runtime refuses a v5 database — correctly. After an eventual v5 deployment, rolling back
therefore needs a prior compatible runtime **and** a restored pre-v5 backup. Backwards schema
compatibility is not attempted.

## D-2026-08-14-2 — Path agreement is not operation agreement, and absence is not conflict (EFE DECISION, ACTIVE)

**Decision.** The relationship vocabulary is `path_agreed`, `claim_only` and `observed_only` — never
a bare `agreed` — every relationship publishes `operation_agreement` explicitly, and it is
`unknown` in this build. **Zero `claim_conflict` relationships are emitted.**

**What today's evidence actually proves.** The machine observation is `git status --porcelain`,
reduced in the durable record to *this project-relative path appears in the changed set*. The
porcelain status letters are not carried into the stored `EvidenceReference`, so the record
genuinely does not contain what was done to the file. A claim of `modified src/foo.py` matched
against an observation that `src/foo.py` changed therefore means the two **name the same file**,
and nothing more.

**Why the word matters more than it looks.** `agreed`, unqualified, invites the reader to supply
the qualification — and they will supply the strongest one available, which is "verified". This
renders on a phone, next to somebody's decision about whether work is done. `path_agreed` plus a
printed "Operation not established", **including on the agreeing rows**, is the whole of what the
evidence supports.

**`claim_only` is not an accusation.** It means unmatched and unverified. A worker may have changed
a file and committed it, in which case `git status` correctly reports a clean tree and there is
nothing to match. Rendering that as a failure would punish the honest case.

**`observed_only` is not evidence of concealment.** The claim set may simply be incomplete, which is
what `ClaimIngestion` records — so the completeness state travels in the same payload, and the two
are read together.

**No conflict, and that is a finding.** To call a claim and an observation *incompatible*, the
observation would have to carry semantics like "this path does not exist" or "this path was
created, not modified". No supported observation carries either. Emitting a conflict from the
absence of a match would manufacture the strongest possible statement out of the weakest possible
evidence. **Absence is not conflict.** When an observation type arrives that can prove
incompatibility, that is where conflict is introduced — as its own decision.

**A rename is two semantic targets, and generic evidence confirms neither.** Both paths observed
means both are `path_agreed` with `operation_agreement = unknown`: two paths changing is not proof
they changed *into each other*. One observed leaves the other `claim_only`, which is a gap and not a
conflict — an untracked destination, an ignored path or a partially staged rename all produce it.

## D-2026-08-14-3 — Semantic project-relative paths are fingerprint inputs; absolute host paths never are (EFE DECISION, ACTIVE)

**Decision.** `input_fingerprint` is a domain-tagged, length-prefixed SHA-256 over exactly the
immutable inputs assembly used. **Project-relative paths are inputs.** Absolute host paths,
provider and session identifiers, read time, live Git state and artifact preview bodies are not.

**Correcting an earlier shorthand.** The working rule during design was "never paths", and it was
too broad. A claimed path is a **semantic identifier for the work**: `src/foo.py` becoming
`src/bar.py` is a different statement about a different file, and a fingerprint that ignored it
would call two materially different claim sets identical — which defeats the only purpose the value
has.

**What an absolute path would break, separately.** A project root, a `/home/...` prefix, a
deployment slot path or a Global Mind path is not an input to assembly at all — assembly never
resolves anything — so hashing one would bind the value to where the deployment happens to live.
The fingerprint would then change when a slot flipped, with no input having changed, and the host's
filesystem layout would be smuggled into a value that gets stored and compared.

**Why length-prefixed rather than joined.** Paths are attacker-influenced text. Joining fields with
a separator lets `a/b:c` and `a` + `b:c` produce one string, so two different input sets fingerprint
alike. The prefix makes every field boundary unambiguous, following `mind/hashing.py`'s discipline
with this module's own versioned tag.

**Why not a JSON dump.** Key order, separator whitespace and number formatting are things a
serializer is entitled to change between releases, and any of them changing would move every stored
fingerprint without a single input having changed.

**`assembler_version` is separate from the bundle's `version`.** A client asks "can I parse this";
a stored fingerprint asks "was this produced by the same rules". Two bundles with identical inputs
and different assembler versions may legitimately differ, and a caller who could not see that would
read an improvement as corruption.

**No `built_at` inside the bundle.** A read-time timestamp would make every read differ from every
other, leaving the fingerprint identifying nothing — and would then invite somebody to add the
timestamp to the fingerprint "for completeness". Response-generation metadata sits on the HTTP
envelope, labelled as presentation.

## D-2026-08-14-4 — Machine observations carry the operation, and `unknown` is an answer (EFE DECISION, ACTIVE)

**Decision.** The Git probe becomes `git status --porcelain=v1 -z`, observations carry a closed
machine change kind and both sides of a rename, and every state Git can report that does not map
cleanly onto one of those words is published as `unknown` rather than approximated.

**This was a loss, not a missing feature.** Cofferdam already ran `git status --porcelain` and the
parser sliced past the two `XY` status characters with `line[3:]`. The operation was fetched and
discarded, which is why PR2 could only ever emit `operation_agreement: unknown`. Two further losses
sat beside it: rename records were split on `" -> "` and only the right-hand path kept, and
`_safe_relative` refused any path starting with a quote — which in human porcelain is *every* path
containing a space, a tab, an arrow or a non-ASCII byte. A file called `has space.txt` produced no
evidence at all.

**Why the machine format, and why the version is pinned.** `-z` makes records NUL-terminated and
paths raw, so a newline, tab or literal `->` inside a filename is just bytes and no separator can be
forged. `--porcelain=v1` is stated explicitly so a future Git changing the meaning of the bare
`--porcelain` cannot change what the parser receives. Output is read as bytes and each field decoded
strictly on its own: `errors="replace"` would turn a non-UTF-8 filename into a *different* filename,
and Cofferdam would publish a path that does not exist.

**The `-z` rename order is the reverse of the human one.** Human porcelain reads `R  old -> new`;
the machine form puts the **destination** in the record and the **source** in the following field.
A parser written from the human output inverts every rename silently, with both paths still looking
plausible. It is pinned by a test that runs the installed Git, not by a comment.

**`unknown` is a first-class member of the vocabulary.** Unmerged states (`UU`, `AA`, `DD`, `AU`,
`UA`, `DU`, `UD`) are mid-conflict and nobody has decided what happened yet. `T` is a type change
that none of the four words describes. `C` is a copy whose source still exists, so calling it a
rename would assert a deletion that did not happen. `MD` is a staged modify and a worktree delete —
two true facts that disagree. The mapping is a table with no branches, so a status this build has
never seen becomes `unknown` rather than a wrong guess.

**No schema change.** `change_kind` and `previous_identifier` are optional fields written only when
present, so a row carrying no machine semantics serialises to exactly the pre-PR3 key set, and the
deserializer already used `.get()`. Old rows read back as `None`, meaning *the operation was never
established* — never *nothing happened*. Schema stays at v5, and the rollback pair the PR2
deployment established is untouched. Bumping the schema for optional dataclass fields would have
compounded rollback complexity for nothing.

## D-2026-08-14-5 — A conflict is two positive machine facts, and it is not a verdict (EFE DECISION, ACTIVE)

**Decision.** `claim_conflict` is emitted only when a stored claim and a machine observation name
the same path and describe **explicitly incompatible** operations. It means the two records
disagree, and nothing else.

**The bar is deliberately high.** Absence is not conflict. A pre-PR3 observation, which carries no
operation, is not conflict. An unmerged or type-changed path is not conflict. A truncated
observation set is not conflict. Each of those is a reason the machine *did not say*, and a
conflict requires that it *did*.

**`created` versus `modified` is `unknown`, not `false`.** A worker that creates a file and then
edits it truthfully says "created", while Git reports whichever the state against HEAD supports.
The two words describe the same work from different vantage points often enough that treating them
as a contradiction would manufacture conflicts out of ordinary sequences. `created` versus `deleted`
**is** incompatible: both cannot describe one path's final state against one HEAD.

**A rename is answered by both paths or not at all.** One table cell cannot express "the source and
destination both match", so renames have their own comparison. Both match: agreed. Same destination
from a different source: incompatible — two rename records cannot both describe one event. A rename
observation with no recorded source: unknown, because half a rename proves nothing about the other
half.

**`path_agreement` stays true for a conflict.** Both records do name the same file; the disagreement
is entirely about the operation. Collapsing the two questions into one field would lose exactly the
distinction that makes a conflict readable.

**It is not a verdict, and the surfaces must not let it become one.** It does not mean the task
failed, the acceptance criteria failed, or the worker was dishonest — a worker that modified a file
and then deleted it produced a conflict and did nothing wrong. The PWA renders it as "Records
differ" with "Both records are kept as they were", styled as something to look at rather than
something that went wrong, and the forbidden-vocabulary scan covers it. **The evaluator is still not
in this milestone.**

## D-2026-08-14-7 — A composite Git status proves two facts, and both decide agreement (EFE DECISION, ACTIVE)

**Decision.** The exact machine status is persisted alongside the change kind, and operation
agreement is computed from the **set** of facts that status proves rather than from a single
collapsed label.

**Because `XY` is two columns.** `X` is the index against HEAD; `Y` is the working tree against the
index. `RM` therefore means *renamed **and** then modified*, `AM` means *added **and** then
modified*, `MD` means *modified **and** then deleted*. Each is two true statements about one path.

**The failure this prevents is specific.** Collapse `RM` to `renamed`, then compare a worker's
truthful `modified` claim against that one word, and the claim looks unsupported — or worse,
contradicted. The fact that would have reconciled it was thrown away by the collapse, not absent
from the evidence. A conflict manufactured that way is exactly the kind of false statement the
milestone exists to prevent, and it would land on the surface that a person reads.

**The rule.** A claim matching **any** proven fact agrees. A claim is contradicted only when it is
incompatible with **every** proven fact. Anything else is `unknown`. One reconciling fact is enough
to stop a contradiction — the conservative direction, and the one that cannot be wrong.

**`change_kind` survives as a label, not as the decision.** It is the primary word a person reads,
and composites whose two facts have no single honest word (`MD`, `AD`, `RD`) are labelled `unknown`
while still proving two facts. Keeping the label and the decision separate is what lets the display
stay simple without the comparison becoming simplistic.

**A rename is never agreed by a status alone.** `R ` and `RM` prove a rename happened; they do not
prove it is *this* rename, because the same destination from a different source is a different
event. `operation_agreement` returns `unknown` for every rename claim, so only the comparison that
uses both paths can agree one. Structural, not conventional.

**Still no schema change.** `change_status` is optional, bounded to two characters, written only
when present, and read with `.get()`. Rows without it fall back to their single label, which is all
they ever knew.

## D-2026-08-14-8 — Machine observation is file-level, because claims are (EFE DECISION, ACTIVE)

**Decision.** The Git probe passes `--untracked-files=all` in literal argv.

**Because the two sides must be comparable.** A `ChangeClaim` names a file. Git's default reports a
wholly new directory as one record, `?? newdir/`, and a directory record can never pair with a claim
about `newdir/a.py`. The observation set was therefore coarser than the thing it is compared
against — and the mismatch would not have read as a granularity difference, it would have read as
"the worker claimed a file Cofferdam never saw change".

**In argv, not configuration.** `status.showUntrackedFiles` could otherwise turn it off from a user
or repository config file. Evidence coverage is not a preference, and a probe whose completeness
depends on a setting is a probe whose completeness cannot be reasoned about.

**Completeness accounts for the larger set.** Enumerating files rather than directories makes the
observation set bigger, so it reaches the caps sooner. Truncation, refused paths and malformed
records all feed one published `machine_observations_complete`, so a set that lost known file-level
evidence is never reported as whole.

## D-2026-08-14-6 — Cofferdam has no pre-work revision, and will not pretend otherwise (EFE DECISION, SUPERSEDED by D-2026-08-15-1)

**Decision.** PR3 does **not** add `git diff --name-status <revision>`. Machine observation remains
what `git status` reports: the index and working tree against the **current** HEAD.

**Because there is no boundary to diff against.** The continuity audit looked for one and found
none. `ClaudeRun` captures no revision when a task starts. `observe_git` runs once, after a result
arrives. `observation.head` is the commit as it stands *at observation time*, recorded as a pointer
for a reader and never compared to anything. Adding a revision diff would have required choosing a
revision, and the only one available is the same HEAD the status already compares against — which
would have been a before/after boundary in name only.

**The consequence is specific and is published rather than hidden: if a worker commits its work,
`git status` reports a clean tree and Cofferdam observes nothing.** The claim then stays
`claim_only`, which is honest — unmatched, unverified — and is asserted by a test that commits the
work and checks the bundle does not turn the absence into a conflict.

**What closing it would take** is a durable pre-work revision, captured when a task or turn starts
and stored where the turn bounds are. That is a decision about what Cofferdam records at task start,
with its own persistence question, and it belongs in its own PR rather than smuggled in behind a
parser improvement.

**Observation completeness is published for the same reason.** When Git reports more changes than
Cofferdam records — through the evidence budget, or because a path failed the safety gate — the
bundle says so, so that an `observed_only` absence is read as "possibly not looked at" rather than
"looked at and not there". Refused paths are counted; the paths themselves are not stored, for the
reason PR1 stores no rejected payload.

**Superseded by D-2026-08-15-1**, which adds the boundary this entry said was missing. The
consequence described above held exactly as written through PR3's deployment, and the closing
paragraph is the specification M2K PR4 was built to.

## D-2026-08-15-1 — The pre-work Git baseline is host-owned, and durable before the worker starts (EFE DECISION, ACTIVE)

**Decision.** Before every worker turn, Cofferdam reads the project's Git revision and working-tree
state itself and commits the result. Schema **v6** adds one additive table,
`task_turn_git_baselines`, keyed `(task_id, turn_number)`. M2K PR4 captures the boundary and
**consumes none of it** — there is no `git diff baseline..HEAD` in this build, `assembler_version`
stays 2, and no route or bridge operation is added.

**Machine-owned is the whole point.** The repository root comes from `verify_root` against the
host's own project registry, re-verified at dispatch. The adapter, the provider, the task prompt and
the API caller cannot choose the root, the revision, the dirty state, or whether capture succeeded.
Every Git argv is a module constant, so no caller text can become a Git argument; a worker that could
name its own starting line would be describing its own homework.

**A missing turn row is not permission to redraw the line.** This is the correction that shaped the
final design, and it is worth stating as its own rule because the first version got it wrong. The
adapter is invoked *before* the turn row is written, so "no row in `task_turns`" covers both "the
worker was never called" and "the worker ran, possibly committed, and Cofferdam died before recording
the turn". A retry in the second case would have captured the worker's own commit as the *pre-work*
boundary, destroying the real one silently.

So permission to replace is a durable fact of its own — `dispatch_state`, deliberately a different
dimension from `capture_state`, which says only how well the repository could be read.
`dispatch_started` commits **before** the adapter call, which is what makes `captured` mean "the
adapter had provably not been reached". Only `captured` is replaceable; everything past it freezes
the revision, object format, head state, tree state, coverage and reason.

**A refusal is recorded and still proves nothing.** `dispatch_refused` exists because learning the
outcome differs from crashing before learning it. It does not re-open replacement:
`AdapterRefusal` is a statement of intent, and `ClaudeCodeAdapter.send_followup` raises it when
`send_turn` fails — *after* bytes may already have reached a live worker's stdin. Distinguishing that
from an early refusal would mean pattern-matching an adapter's message text, which the core must
never do. A retry after a refusal reuses the same reserved turn number and dispatches against the
same boundary, which is both the safe answer and the correct one: the earliest boundary for a turn
number precedes every attempt at it.

**Ordering is structural, not temporal.** "Pre-work" is not "shortly before". The capture is a
committed write on the only two paths that open a turn, `TaskService._start` and
`TaskService.send_followup`, immediately before `adapter.start` and `adapter.send_followup`, under
the dispatch lock. It does not depend on adapter cooperation, and a test adapter asserts from inside
its own first instruction — on a separate read-only connection, so uncommitted rows cannot satisfy
it — that the boundary is already durable.

**The foreign key names `tasks`, not `task_turns`, and that follows from the ordering.** On both
paths the adapter is invoked *before* the turn row is written, deliberately: an adapter refusal must
leave no turn behind, and a follow-up must never be recorded as delivered before the session took it.
The tidy design — write the baseline inside `_open_turn_locked` beside `task_turns` — would therefore
have landed the boundary *after* the worker started, and would have made the honest outcome
"captured, then the adapter refused, so the turn never opened" unrepresentable. A composite key was
considered and rejected for exactly that reason; task ownership still travels through the cascade.

**Nothing is invented.** `present` stores the resolved object id; `unborn` stores no revision and
specifically not the empty-tree object; `unavailable` and `not_a_repository` store none either. The
object format is **read** via `--show-object-format` rather than assumed, because Git 2.29 shipped
SHA-256 repositories and this host runs 2.53 — "forty hex characters" stopped being a rule. The
stored value is validated as a resolved identity, which refuses `HEAD~5`, a branch name, a path and
every other revspec by construction: a revspec is a program, a boundary must be an identity.

**A HEAD that moves across the observation is not resolved to a guess.** HEAD is read, status is
inspected, HEAD is read again; disagreement means neither read describes the moment, so the attempt
is retried a bounded three times and then recorded as explicitly unstable. Bounded, not "until it
settles" — a repository being rewritten in a loop must not hold a dispatch open.

**A pre-existing dirty tree is a durable fact, and not an accusation.** A project can be dirty for a
hundred innocent reasons. Recording it is what lets PR5 say "changes since this revision did not
necessarily start from a clean tree" instead of implying they did. `clean` may never rest on an
incomplete status read — a bounded read cannot conclude that nothing changed — and both the value
type and a schema CHECK refuse that combination.

**Git evidence is not a precondition for somebody's work.** A project that is not a repository, or
cannot be read, still runs its task; what changes is that the unavailability is durable *before* the
worker starts, so a later reader finds an explicit unavailable boundary rather than a silence it
could mistake for a clean tree. A missing row means *no boundary was recorded* and never *the tree
was clean*, which is also the answer for every turn predating v6 — historical turns are **not
backfilled** from timestamps, the current HEAD, the reflog or guessed ancestry.

**The limit is published rather than glossed.** A clean host-owned snapshot does not prove only the
worker changed the repository afterwards; a person with a shell, an editor autosave or another tool
can modify the same tree concurrently. What a stored boundary supports is machine-observed change
since a recorded point — a statement about records. It is not proof of causation, and nothing built
on it may say "the worker did this" in those words.

**Still no evaluator**, no verdict, no confidence, no risk level, no acceptance criteria and no
check runner.

## D-2026-08-16-1 — The assessment route refuses the Actions bridge credential, on purpose (EFE DECISION, ACTIVE)

**Decision.** `GET /api/tasks/{task_id}/turns/{turn_number}/assessment` is guarded by
**`require_token`** and must **not** be changed to `require_task_caller`. This is recorded as a
decision precisely because the two dependencies look interchangeable at a glance and ten neighbouring
task routes use the other one — a future refactor that "unifies" them for consistency would silently
widen the assessment surface to a caller class it was deliberately kept from.

**What the distinction actually is.** `require_task_caller` accepts *two* credentials: the device
token and the Actions bridge's own. `require_token` accepts only the device token and has never heard
of the bridge credential, so a bridge request arrives as an ordinary unauthenticated one and is
refused with 401. That is a **stronger guarantee than an explicit rejection check**, which a later
edit could delete without any test noticing the class of caller it used to exclude.

**Why an assessment is further from the bridge than a task is.** The bridge is a private Custom GPT
surface. It may create a task, ask what happened to one, answer a question and finish it — the
operational verbs. An assessment is Cofferdam's judgement about somebody's work measured against what
they asked for, which is a different kind of statement about a person's private working life. The
`EvidenceBundle` route set this precedent for the same reason; PR8 follows it for a stronger version
of it, because an assessment is the evidence *plus a machine's reading of it*.

**Both halves are asserted.** The tests prove the bridge credential is refused here **and** still
accepted where it belongs, so the refusal can never be mistaken for a broken credential. Verified
again live at the PR8 deployment: with one bridge credential, `/assessment` returned 401 while
`/api/tasks/{id}`, `/result` and `/clarifications` all returned 200.

**Not a consistency defect.** If a future change genuinely needs the bridge to read assessments, that
is a scope decision with its own review, not a refactor. Changing the dependency is the change; the
route text staying the same does not make it a small one.

## D-2026-08-16-2 — Lifecycle, acceptance and verification reach are three axes, and collapsing any two is the failure (EFE DECISION, ACTIVE)

**Decision.** Cofferdam keeps three questions structurally and semantically separate, and no future
aggregate may merge them:

- **Worker lifecycle** — *what happened to execution?* (`completed`, `failed`, `interrupted`,
  `cancelled`.)
- **Acceptance** — *what do the recorded criteria and evidence establish?*
- **Verification reach** — *how far could Cofferdam see?*

**`completed` does not imply `met`.** The live database is the demonstration: 10 completed tasks and
zero evaluations. A worker that ran to a clean stop has told you about its own control flow and
nothing about whether it did what was asked. **`failed` does not imply `not_met`** either — a turn
can fail after satisfying every criterion recorded for it, and reporting that as unmet would be a
false negative invented by the aggregate.

**Why this is the load-bearing rule of the whole milestone.** Every other guarantee M2K built —
criteria frozen before dispatch, evidence assembled from machine observation, evaluation frozen after
close — exists to stop a worker's own account of its work from being the verdict on it. An aggregate
that read lifecycle as acceptance would reintroduce exactly that, in one line, at the end.

**Vocabulary follows from it.** Acceptance never uses `success`, `failed` or `passed`: those words
already belong to lifecycle, and a reader who sees one cannot tell which domain it came from. The
acceptance words are `met`, `not_met`, `incomplete` and `not_assessable`.

## D-2026-08-16-3 — Per-turn assessment has two dimensions, and known failure dominates uncertainty (EFE DECISION, ACTIVE)

**Decision.** A future per-turn aggregate publishes **two dimensions**, not one overloaded enum.

**Availability**, derived from the criteria state alone:

| criteria state | availability | reason |
| --- | --- | --- |
| `present` | `assessable` | — |
| `not_provided` | `not_assessable` | `no_structured_criteria` |
| `legacy_unknown` | `not_assessable` | `historical_criteria_unknown` |

Neither absent case may ever be rendered as `met`, `success`, `passed`, or an empty pass.
`not_provided` means *Cofferdam knows none were supplied*; `legacy_unknown` means *Cofferdam does not
possess the question*. They are different facts and their reasons stay distinct — collapsing them
would turn "we never asked" and "we cannot know what we asked" into one sentence.

**Acceptance outcome**, which exists **only** when criteria are `present`, with the closed V1
vocabulary `met` / `not_met` / `incomplete` and this ordered rule:

1. **Any deterministic `not_met` ⇒ `not_met`.** One known unmet required criterion is already
   sufficient to know the turn's recorded requirements were not all established, regardless of how
   many others are unverified. This is an *acceptance* result and explicitly **not** a lifecycle
   failure.
2. **Otherwise any `unverified` ⇒ `incomplete`.** Never `not_met`. This preserves the doctrine the
   evaluator was built on: an evidence limitation is not a finding about the work.
3. **Only every-criterion-`met` ⇒ `met`.**

**The order is the point.** Known failure dominates, uncertainty blocks `met`, and nothing else
reaches `met` — monotonic and conservative in the direction that cannot manufacture good news.

**What `met` means, exactly.** *The acceptance criteria recorded for this turn are all established as
met by the current assessment model.* It does **not** mean the task succeeded, the worker succeeded,
the user's full intent was captured, or that a later turn cannot regress it.

**Manual criteria cap the outcome.** A `manual` criterion always evaluates to `unverified`, because
no human-answer channel exists, so **any `present` snapshot containing one cannot currently reach
`met`** — it is capped at `incomplete`. This is recorded rather than worked around. Manual completion
must never be inferred from worker prose, a PWA interaction, a claim, or a model judgement; a
human-answer channel is new authority and new state and deserves its own reviewed design.

**Blockers are orthogonal context, not a fourth outcome.** `requires_human` must not become a
competing aggregate value: when a turn has both an unanswerable manual criterion and an incomplete
machine observation, a single value has to suppress one of them. Boolean context beside the outcome —
`requires_human`, `machine_verification_incomplete` — lets the turn say `incomplete` and say exactly
why, and is what a caller can actually compose.

**`claim_conflict` is excluded from aggregation entirely.** It is a disagreement between an adapter's
record and the machine's. It is not a criterion result, not a task failure and not an aggregate
blocker, and placing it near an outcome is precisely how a reader would come to treat it as one. It
stays on the evidence surface as audit context.

## D-2026-08-16-4 — There is no task-level acceptance, because criterion continuity does not exist yet (EFE DECISION, RATIONALE SUPERSEDED IN PART BY D-2026-08-17-15)

> **Status note, 2026-08-17.** The **conclusion stands**: there is still no task-level acceptance and
> Cofferdam still reports it as unavailable. The **stated reason no longer holds.** This decision
> named the missing fact as criterion continuity, and PR10–PR12 built it — so the blocker it
> identified is gone for a *target-turn* aggregate, which
> [D-2026-08-17-15](#d-2026-08-17-15--the-target-turn-aggregate-is-derived-pure-and-versioned-separately-efe-decision-active)
> now specifies. A *global task* verdict remains out of scope for a different and still-undecided
> question: which turn's requirements a whole task should be judged against. The original text stands
> unedited as history — its two worked counter-examples are exactly why that question is still open.

**Decision.** Per-turn acceptance is well defined (D-2026-08-16-3). **Task-level acceptance across
multiple turns is unavailable**, and Cofferdam will report it as unavailable rather than invent a
rule. This is the deliberate answer, not a deferral for lack of time.

**Both obvious rules are demonstrably wrong.**

*Accumulate all turns* — turn 1 requires `foo.py` created; turn 2 requires `foo.py` removed. Treated
as simultaneously active, the task's own requirements contradict each other, and no outcome is
correct. The second turn was a legitimate change of mind, and an aggregate that cannot represent that
reports a fault that does not exist.

*Latest turn only* — turn 1 requires feature X and tests; turn 2 adds logging. Honouring only turn 2
silently drops X and the tests from acceptance, and the task can report `met` while its original
requirements were never established. A later turn routinely **extends** earlier intent without
restating it.

**The missing fact is the same in both.** Every turn may carry its own immutable criteria snapshot,
and Cofferdam persists **nothing** about the relationship between snapshots: whether a later one
replaces, extends, narrows, supersedes one specific criterion, reverses one, or is an independent
follow-up concern. Without that, no composition across turns can be correct, and picking one anyway
would encode a guess as a verdict.

**Reporting the gap is the safe behaviour.** "Per-turn acceptance is available; task-level acceptance
is not yet defined" is a true sentence a caller can act on. A confidently wrong task verdict is not.

## D-2026-08-16-5 — Criterion continuity is explicit, pre-dispatch, and never authored by the worker (EFE DECISION, ACTIVE)

**Decision.** The prerequisite for any task-level aggregate is a durable representation of criterion
continuity, and its shape is constrained now even though none of it is built.

**Explicit, never defaulted.** A new turn must state its continuity mode rather than inherit one.
`replace` and `extend` are not distinguishable by inspecting the criteria, and a wrong default is
applied silently to every task that never thinks about it.

**Snapshot-level relation plus criterion-level relations.** A `supersedes_snapshot_id` alone cannot
express the common case — a later turn superseding one requirement while its siblings stay live — so
per-criterion relations are needed as well. Snapshot-level alone forces an all-or-nothing choice that
matches neither of the two failure examples in D-2026-08-16-4.

**Content fingerprints are not lineage.** Identical criterion text does not prove semantic lineage —
two turns can require the same file changed for unrelated reasons — and differing text does not
disprove it. Fingerprint matching may be an aid; it may never be the authority.

**The planner or the user is the authority. The worker is not, and the adapter never is.** A worker
that could declare its new criteria supersede its old ones could retire the requirement it had just
failed, which is the self-grading this milestone exists to prevent. Adapters are excluded absolutely,
on the same footing as every other adapter-supplied fact in Task Core.

**Frozen pre-dispatch, alongside the criteria snapshot.** For the same reason the criteria and the
Git baseline are: a boundary a worker can move after seeing its own results is not a boundary.

**It requires an additive schema version**, and it is a **hard prerequisite** for a runtime
task-level aggregate — that aggregate is not to be attempted before this exists.

## D-2026-08-16-6 — A future aggregate is versioned separately and derived on read (EFE DECISION, ACTIVE)

**Decision, two parts.**

**Independent version.** A future aggregate carries its own code-owned semantic version —
`AGGREGATOR_VERSION` or equivalent — distinct from the schema version, `ASSEMBLER_VERSION`, the
criteria model version and `EVALUATOR_VERSION`. Composition doctrine changes for its own reasons, and
a change from one doctrine to another must never silently reinterpret answers produced under the old
one. Cofferdam already keeps four such versions apart for exactly this reason; a fifth concern gets a
fifth version rather than borrowing the nearest.

**Derived on read, not persisted.** A per-turn aggregate is a pure deterministic function of an
immutable `EvaluationRecord` and its criteria snapshot. Persisting it would store nothing that cannot
be recomputed, while adding a write path to a surface whose central property — asserted repeatedly in
PR8 — is that reading it mutates nothing. Deriving it keeps that intact and makes a doctrine change a
re-render rather than a data migration.

**The counter-argument, and its answer.** Historical audit may later want *what did Cofferdam say at
the time*. That is an argument for recording the **aggregator version beside the answer**, not for
persisting the answer alone, and it should be taken as its own decision when a real need appears
rather than pre-emptively.

**Ordering: this doctrine precedes the named check runner.** The runner introduces the first
project-scoped command execution authority, a new recorded result type, probably a new criterion kind
and a `check_id`, invocation and result persistence, timeout and output policy, and an evaluator
semantic expansion that would move `EVALUATOR_VERSION`. Built first, every one of those results would
feed a consumer with no defined contract. Its trust boundary is unchanged and still binding
(D-2026-08-11-7): host-owned definitions by stable id, literal `argv`, `shell=False`, validated
project `cwd`, bounded timeout, bounded output, off by default per project, and **neither the caller
nor the adapter ever supplies command text**.

## D-2026-08-16-7 — Criterion continuity is a stored fact with three states, and absence is one of them (EFE DECISION, ACTIVE)

**Decision.** M2K PR10 persists what a turn's criteria say about the turn before them. Schema **v9**
adds `task_turn_criteria_continuity` and `task_turn_criterion_supersessions`, additively, created
empty. It is the prerequisite D-2026-08-16-4 and D-2026-08-16-5 named, and it **computes no
aggregate** — a lineage edge is not a verdict, and no code in this build could turn one into one.

**Three read states, and the third is a missing row.** `declared` and `not_declared` are stored;
`legacy_unknown` is what absence means and is never written. This is the exact shape criteria already
use, for the identical reason.

**An undeclared dispatch writes `not_declared` rather than nothing**, and that is the decision inside
the decision. Omitting the row would make "nobody declared a relationship for this turn" and "this
turn ran before Cofferdam could record one" the same observation forever, and only the first is
recoverable. `not_declared` is **not** `extend`, **not** `replace`, **not** `independent` and **not**
preserve-previous: it is the absence of an answer, recorded, and it deliberately leaves a future
task-level aggregate unavailable for that turn rather than quietly guessing.

**Four modes, and `independent` is not one of them.** `root` — no predecessor exists. `extend` — the
predecessor's active requirements remain and this snapshot adds. `replace` — the prior active set is
wholly superseded, with nothing deleted and no ceremony enumerating what "all of them" means.
`revise` — prior requirements remain **except** those named by explicit supersession relations.

`independent` was in the PR9 discussion and does not survive the question a mode must answer: *what
happens to the requirements that were already live?* It answers neither "they remain" nor "they are
superseded". If they remain that is `extend` whatever the intent was called; if they do not that is
`replace`. A third word for the same two outcomes would let a caller say something no aggregate could
compose, which is precisely the ambiguity this vocabulary exists to remove.

**`root` is recorded mechanically and is still checked.** It is a structural claim — *this task has
no earlier criteria snapshot* — rather than an inference about intent, which is what makes it safe to
derive. It is nonetheless verified against the database rather than believed, and refused when an
earlier snapshot exists. A first turn whose criteria are `not_provided` is still `root`: the mode
describes lineage, the criteria snapshot already records that nothing was required, and saying
otherwise here would create a second, contradictory place to look.

**A `not_provided` follow-up proves nothing about continuity.** The absence of structured criteria is
not evidence that prior requirements were preserved, replaced or unchanged, so such a turn takes the
ordinary path: `not_declared` unless somebody declared otherwise. Intent is never inferred from an
empty criteria set.

**No backfill, ever.** Historical turns get no continuity row. Deriving one from a prompt, a title,
worker prose, a claim, or from "the latest turn wins" would manufacture an intent nobody expressed,
which is the failure this whole milestone is shaped to prevent.

## D-2026-08-16-8 — Supersession is a bounded many-to-many between durable criterion ids (EFE DECISION, ACTIVE)

**Decision.** Partial revision is expressed by explicit `(current criterion, predecessor criterion)`
edges, stored in `task_turn_criterion_supersessions`, and required by exactly one mode — `revise`.
`extend` retires nothing and `replace` retires everything, so in both an edge would contradict the
mode it was filed under and is refused.

**Many-to-many, with no directional special cases.** One old criterion may be superseded by several
new ones — a requirement split in two — and several old ones by a single new one — two requirements
merged. Both are ordinary domain events. Forbidding either would have meant inventing a direction the
domain does not have, and pair uniqueness is the only structure actually required.

**Both sides are durable ids, and this is the security property.** Matching description, matching
criteria fingerprint, matching path and matching ordinal are all things two unrelated criteria can
share; differing text does not disprove lineage either. **Similarity is never authority.** The
predecessor criterion is additionally validated so that a declaration cannot retire a requirement
from a turn it never claimed to stand on. *(M2K PR12 corrected how that is checked: originally
"belongs to the declared predecessor snapshot", now "is active in the declared predecessor's resolved
active set". See D-2026-08-16-14 — the rule this sentence states is unchanged; the mechanism was too
narrow to enforce it faithfully.)*

**The caller names the current side by ordinal, never by id.** Current criterion ids are minted
inside the same reservation moments earlier, so a caller that could supply one would be choosing a
durable identity. It supplies the ordinal it already knows and the store resolves it.

**The predecessor is a snapshot id, not a turn number.** A turn number worked out at read time would
silently re-point if a reservation were replaced; a snapshot id names one immutable row forever. It
is validated to exist, to belong to **this task** — lineage never crosses tasks — and to come from a
**strictly earlier** turn, which makes naming the current or a later turn a cycle rather than a
lineage.

**Bounded at 64 relations, refused rather than trimmed.** Generous against the 32-criteria limit and
finite, because an unbounded lineage declaration is an unbounded write a caller controls. A silently
dropped supersession would leave a requirement live that somebody had retired, which is worse than a
rejection that says so.

## D-2026-08-16-9 — Continuity freezes with the criteria, and only the declaring caller may set it (EFE DECISION, ACTIVE)

**Decision.** A continuity declaration is committed against the same reserved turn number as the
criteria snapshot and the Git baseline, immediately after the snapshot it describes — it names that
snapshot by an identity only that write could mint — and **before `dispatch_started`**. All three
freeze in the same call and by the same rule: `captured` is the only replaceable state.

**The adapter's first instruction is the assertion point.** A test adapter reads, on a separate
read-only connection so uncommitted rows cannot satisfy it, that the criteria snapshot, its items,
the continuity row and the Git baseline are all durable and all already `dispatch_started`, while
`task_turns` still has no row.

**Retry, refusal and crash all leave it alone.** A retry of the same reserved turn dispatches against
exactly the lineage the first attempt did, even when it submits a different one. An `AdapterRefusal`
does not reopen replacement — a refusal is a statement of intent, never a proof the worker was
untouched — and the argument is if anything stronger here than for criteria: re-pointing a turn's
lineage after a worker may already have acted would re-parent completed work onto requirements it
never stood on. A crash after `dispatch_started` leaves the declaration frozen across restart. A
genuinely new turn may declare afresh, and only that.

**Authority is the user or a future host-owned planner. Never the worker, never the adapter.**
`AdapterOutcome` and `TaskContext` carry no continuity field and gain none. Worker prose, claims, Git
evidence, the evaluator and a read of the assessment surface can none of them create an edge. A
worker that could declare its new criteria supersede its old ones could retire the requirement it had
just failed.

**No public surface.** Continuity is an internal `TaskService` argument with no HTTP request field,
no bridge Action and no PWA control — exactly the boundary PR6 drew for criteria and did not widen.
Every caller that exists passes nothing and is unaffected. A read surface, if one is ever wanted, is
its own review.

**`CONTINUITY_MODEL_VERSION = 1`**, code-owned and distinct from `SCHEMA_VERSION`,
`CRITERIA_MODEL_VERSION`, `ASSEMBLER_VERSION` and `EVALUATOR_VERSION`, and bound into a deterministic
`continuity_fingerprint` over the state, mode, both snapshot identities and the relations in
canonical order. No clock, no rowid, no minted id, no provider or session identifier and no host path
reaches it. **There is deliberately no `AGGREGATOR_VERSION`**: nothing aggregates, and the constant
would imply something does.

## D-2026-08-16-10 — The active criterion set is derived on read, never stored (EFE DECISION, ACTIVE)

**Decision.** "Which criteria are in force at this turn" is computed from PR6's immutable criteria
snapshots and PR10's immutable continuity declarations every time it is asked. It is **not** written
to any table, and M2K PR11 adds **no schema version** — v9 stays.

**Why derive.** Every input is already immutable and already fingerprinted, and the function over
them is deterministic and versioned, so the answer can be reproduced at any later date from rows
nobody may edit. Storing it would buy nothing and cost three things: a new write path to get wrong, a
new recovery path for a crash between the sources and the cache, and a second place for the truth to
live that could disagree with the first. A cached active set that drifted from its sources would be
worse than no cache, because it would look authoritative.

**`RESOLVER_VERSION = 1`**, code-owned and distinct from `SCHEMA_VERSION` (the shape of the tables),
`CRITERIA_MODEL_VERSION` (what a criterion is), `CONTINUITY_MODEL_VERSION` (what a declaration
means), `ASSEMBLER_VERSION` (how a bundle is built) and `EVALUATOR_VERSION` (how a criterion is
answered). Six constants because six things move for six reasons, and a reader must be able to tell
which one did. It is bound into the resolved fingerprint, so a future version 2 that resolved the
same rows into a different set produces a **visibly different** identity rather than silently
reinterpreting a stored one.

**Still no `AGGREGATOR_VERSION`, and this is not a step towards one.** A resolved active set says
*what is currently required*. It contains no verdict, no acceptance outcome and no count of what was
met, and a **resolved empty set means the declared requirement set is empty — never that the task
passed**. D-2026-08-16-4 stands: task acceptance remains unavailable.

## D-2026-08-16-11 — `replace` is a lineage cut point, so unknown history does not poison a task (EFE DECISION, ACTIVE)

**Decision.** Resolving an explicit `replace` does **not** require the predecessor's active set. The
current snapshot becomes the active set outright.

**Why.** `replace` says the prior requirement set is wholly superseded — whatever it was. Demanding
that it first be *known* would make the resolver require an answer it has just been told is
irrelevant, and the consequence would be permanent: a task with one `legacy_unknown` turn from before
continuity existed, or one turn nobody declared anything for, could never have a knowable requirement
set again, no matter what anybody declared afterwards. That is too conservative to be honest. An
authoritative `replace` is exactly how a user or a future planner re-establishes a known requirement
set after an unknown segment.

**The contrast is the argument.** `extend` and `revise` are statements *about* the prior active set —
"those remain, plus these", "those remain except the ones I name" — so an unknown predecessor makes
them genuinely unanswerable, and both are **unavailable** rather than best-effort. `root` needs no
predecessor at all. Only `replace` both names one and does not depend on it.

**Cutting a dependency is not ignoring a declaration.** A `replace`'s predecessor is still validated
to exist, to belong to this task and to come from an earlier turn. What is skipped is the traversal,
not the check. A malformed `root` is likewise **never reinterpreted as `replace`** — that would be
the resolver inventing the declaration PR10 requires somebody to make.

**The fingerprint says so honestly.** A predecessor a `replace` did not traverse is **not** bound
into the resolved fingerprint, and its turn does not appear in the lineage trace. Two turns replacing
with identical criteria agree whatever came before them, because nothing before them played any part
in either answer.

## D-2026-08-16-12 — A supersession is valid only against an *active* criterion, and a stale one fails closed (EFE DECISION, ACTIVE)

**Decision.** When `revise` retires a criterion, that criterion must be **active in the resolved
predecessor set**. Existing somewhere in the task's history is not enough. A relation whose old side
is not active makes the resolution **unavailable** with
`supersession_target_not_active`; it is never skipped.

**Why not skip it.** Ignoring the stale edge would produce an active set that no declaration asks
for — the predecessor's set unchanged, plus the current criteria — and present it as authoritative.
The declaration said *retire this*, the rows say *this was already retired two turns ago*, and the
only honest reading of that contradiction is that the stored lineage does not determine an answer.
Silently resolving would be exactly the guessing the whole milestone exists to prevent.

**Where it can come from.** PR10's write validation requires a relation's old side to belong to the
declared predecessor's own snapshot, and every criterion of a snapshot is active in that snapshot's
own resolved set, so a valid write cannot produce a stale edge. This is therefore a **read-time
invariant against corrupted state** — a restored database, a hand-edited row, a future version with a
bug — and it is checked because "the schema should make this impossible" is not a reason for a read
to trust it. The same doctrine covers a snapshot mismatch, a cross-task or later-turn predecessor, an
impossible `root`, a mode disagreeing with its relations, a duplicate active criterion id, a cycle,
and a chain past `MAX_LINEAGE_DEPTH`. **Nothing is repaired on read.**

**A known PR10 limitation, recorded rather than widened — and since RESOLVED by D-2026-08-16-14.**
As written, PR10 required the old side to belong to the *declared* predecessor's own snapshot, so a
`revise` could not retire a criterion it merely **inherited** through an earlier `extend` unless it
declared that earlier snapshot as its predecessor — which then cut the intervening turn's own
criteria out of the lineage. PR11 deliberately did not loosen that from inside a read; **M2K PR12
loosened it at the write, where it belonged**, so the write-time rule is now this decision's
active-set rule. Everything else in this decision stands unchanged, including that a stale target
fails closed at read time.

## D-2026-08-16-13 — Lineage order is submission order, and lineage is read under one snapshot (EFE DECISION, ACTIVE)

**Decision, ordering.** The resolved active set is ordered **inheritance first**: surviving inherited
entries keep their relative order, superseded entries are removed **in place** with nothing promoted
into the hole, and this turn's own criteria follow in stored `ordinal` order. It is **never** sorted
by criterion id, description, path or fingerprint.

**Why.** The order somebody wrote their requirements in is a fact about the requirements — the same
reason D-2026-08-14 kept `ordinal` stored rather than derived from a rowid, and the same reason
`validate_criteria` preserves the caller's order instead of sorting it. Sorting by id would order by
a minted handle; sorting by text would order by prose; either would present a list nobody submitted.

**Decision, consistency.** The whole lineage fetch runs inside **one deferred read transaction**, not
a chain of autocommit reads. Resolution walks several turns' snapshots and declarations, so it is
precisely the read that could otherwise inherit an active set from before another process's commit
and supersede against rows from after it — a graph that never existed. In WAL mode a deferred
transaction pins the read snapshot at the first statement without blocking any writer, so the cost is
nothing. It is deferred rather than `IMMEDIATE` because a reader must never take the write lock.

**Decision, purity.** Fetching and resolving are separate. The resolver takes a frozen input graph
and reaches no SQLite, filesystem, Git, subprocess, socket, provider, environment or clock, and
mutates nothing — asserted from the syntax tree, again at runtime with those callables poisoned, and
again by deleting the project repository and getting a byte-identical fingerprint. A pure function
that *could* write would be pure only by convention, and the claim being defended is that a
resolution describes what was declared and frozen rather than what the world looks like now.

**Bounded, because "impossible" is not a termination proof.** `MAX_LINEAGE_DEPTH = 256` and a visited
set of turns. PR10's strictly-earlier rule should make a cycle unreachable; a read that runs at
start-up must still **answer** rather than hang if it meets one.

## D-2026-08-16-14 — A revise may retire whatever its predecessor actually stands on (EFE DECISION, ACTIVE)

**Decision.** For `revise`, the allowed old-side criterion ids are exactly **the criterion ids in the
resolved active set of the declared predecessor**. Not the ids the predecessor's snapshot physically
owns. Supersedes the mechanism — not the intent — of the corresponding sentence in D-2026-08-16-8,
and resolves the limitation recorded in D-2026-08-16-12.

**Why the old check was wrong.** Its stated purpose was that a declaration must not retire a
requirement from a turn it never claimed to stand on. A requirement introduced at turn 1 and still
live at turn 2 through an `extend` **is** something turn 2 stands on, so refusing turn 3 permission
to retire it enforced something narrower than the sentence meant. The only workaround was to declare
turn 1 as the predecessor, which silently dropped turn 2's own criteria from the lineage — a worse
outcome reached by following the rules.

**What is still refused, and this is the substance.** A criterion an earlier `revise` retired; one a
`replace` cut away; one belonging to another task; an id naming no criterion; a criterion of the
*current* snapshot used as an old side. Historical existence has never been authority and still is
not. Each refusal is atomic: a declaration with one valid relation and one invalid one persists
nothing, so there is no partial lineage to clean up.

**A `revise` over an unresolvable predecessor is refused before dispatch.** `not_declared`,
`legacy_unknown`, malformed, cyclic or over-deep — in all of them there is no set for the revision to
be a revision *of*. Storing it and leaving the reader to reject it later would durably record a
relationship that can never be honoured. It is **never** downgraded to `replace`: inventing a
declaration the caller did not make is the one thing this whole vocabulary exists to prevent. This
narrows what PR10 accepted, deliberately, and it is the only narrowing here.

**`replace` is untouched and remains a cut point.** It validates its predecessor's identity and does
not require its active set, so D-2026-08-16-11's recovery property is intact: an unknown segment
followed by an explicit `replace` still resolves. `extend` carries no relations and gains no check.

**No version moves, and this is the crux.** `CONTINUITY_MODEL_VERSION` stays 1 and `RESOLVER_VERSION`
stays 1, because **what a stored relation means is unchanged**. The row always said *this new
criterion retires that old one* and never carried a claim about which snapshot the old one sat in;
the foreign key names `task_turn_criterion_items` at large, which is why schema stays **v9** and why
PR11's resolver already interpreted an inherited relation correctly. `continuity_fingerprint` is
byte-identical for the same declaration, no existing row is rewritten, and nothing is revalidated or
backfilled at start-up. What changed is which declarations are *accepted*.

**The resolver-version dependency, stated explicitly.** Write-time validation now depends on the
current code-owned active-set semantics, so a future `RESOLVER_VERSION = 2` could in principle
change which *new* declarations are accepted. The persisted fact remains the explicit declaration,
so already-stored relations are unaffected either way. The rule adopted here is **(A): a future
resolver change must preserve acceptance of relations that were valid when written.** A change that
would not — one that shrinks what counts as active — is a continuity-model concern rather than a
resolver-only one, and must go through a `CONTINUITY_MODEL_VERSION` review at that point. No schema
or version is added now to speculate about it.

**One implementation.** Validation calls the pure resolver over the shared lineage fetch rather than
reimplementing the fold, and it runs **inside the write transaction** so validation and persistence
observe one database state. A second copy of the algorithm could disagree with the read path, and the
disagreement would surface only as a stored relation the reader refuses — the worst possible place to
discover it. **PR11's read-time checks are kept regardless**: write-time prevention is an additional
guarantee, and a restored, imported or future-buggy database can still present a stale relation.

## D-2026-08-16-15 — Every v1 acceptance predicate is a turn-change observation, not a final-state assertion (EFE DECISION, ACTIVE)

**Decision.** The three evidence predicates this build can express — `path_changed`,
`path_operation` and `rename` — each assert **"the worker did X during this turn"**. None asserts
**"the project now satisfies X"**. This is recorded as doctrine because the distinction is invisible
in the criterion vocabulary and decisive for everything built on top of it.

**Read from the implementation, not inferred.** `evaluation._evaluate_path_changed`,
`_evaluate_path_operation` and `_evaluate_rename` each consult
`_attributable_observations(bundle, path)` and nothing else — observations attributed to *the turn
being evaluated*. There is no code path in which any of them reads repository state, a prior turn's
observations, or anything outside the one `EvidenceBundle` handed in.

| predicate | class | asserts | monotonic | can absence at a later turn prove `not_met`? | meaningfully re-evaluable at a later turn? |
|---|---|---|---|---|---|
| `path_changed(P)` | action/change | the worker produced a resulting change at P **this turn** | no | no — it proves only that *this* turn did not change P | mechanically yes, semantically no |
| `path_operation(P, OP)` | action/change | the worker performed OP at P **this turn** | no | no | mechanically yes, semantically no |
| `rename(S, D)` | action/change | the worker renamed S→D **this turn** | no | no | mechanically yes, semantically no |
| `manual` | undecidable | a person must check | n/a | no — it is never decided by machine | no |

**Consequence, and it is the load-bearing one.** Re-evaluating an *inherited* criterion against the
target turn's bundle is a **category error**, not an approximation. `path_operation(foo.py, created)`
asked at turn 2 means "did turn 2's worker create foo.py". For a well-behaved turn 2 that correctly
leaves the file alone, the honest answer is *no*, and `_evaluate_path_operation` renders that as
`not_met` the moment closure is complete. The naive approach does not risk a false negative — it
manufactures one reliably, and it does so most often when the work was **correct**.

**Two classes follow from this, and are not invented for convenience.** *Action/change* criteria bind
to the turn that asked them and cannot be re-asked later. *State/invariant* criteria — which would
assert something about the resulting project rather than about worker behaviour — could be evaluated
at any turn. **Every criterion this build can express is in the first class; the second class is
empty** until final-state predicates exist. No class marker is added to the schema now: a
discriminator with one inhabited value is a column that teaches a reader the wrong thing.

## D-2026-08-16-16 — Continuity is requirement lineage, never evaluation lineage (EFE DECISION, ACTIVE)

**Decision.** A continuity declaration says which **requirements** remain active. It says nothing
whatever about evidence or evaluation. Specifically, `extend` and a surviving `revise` criterion do
**not** imply any of:

* that the earlier evidence is still current;
* that the earlier `EvaluationRecord` is still authoritative;
* that no later work regressed the requirement;
* that current repository state satisfies it.

**Why the conflation is tempting and wrong.** "The requirement is still active" and "the requirement
is still met" are one short step apart in English and a whole layer apart in fact. Continuity is
authored by a person or a planner **before** the turn runs (D-2026-08-16-9); it is an intent, frozen
pre-dispatch, and an intent cannot certify a state of the world it precedes. Letting lineage carry
evaluation would mean a declaration made before any work happened silently vouched for the work.

**Therefore active-criterion resolution (PR11) and current-acceptance binding are separate layers**,
and the second does not exist. PR11 deliberately answers only the first question and contains no
result vocabulary at all.

## D-2026-08-16-17 — No stored evaluation is authoritative for a later turn, in either direction (EFE DECISION, ACTIVE)

**Decision.** An `EvaluationRecord` is authoritative for **its own turn and no other**. It is never
carried forward as the current answer for a later target turn, and this holds for all three result
values. There is no optimistic carry-forward and no pessimistic one.

**`met` is not monotonic.** Turn 1 creates `foo.py` and A is `met`. Turn 3 deletes it. A is still
active, the stored `met` is still a true statement about turn 1, and it is a false statement about
turn 3. Reusing it would report a satisfied requirement over a broken one.

**`not_met` is not monotonic either, and this direction matters more than it looks.** Turn 1 leaves A
`not_met`. The user says "fix it", continuity preserves A, and turn 2 repairs it. A stale `not_met`
carried forward would tell somebody their completed fix had failed — the system contradicting work it
watched succeed. The pessimistic direction is not the safe one; it is just the other wrong one.

**`unverified` is emphatically not permanent.** It usually records a *limitation of the observer* —
inexact attribution, a dirty pre-work boundary, unread coverage — and every one of those can be
absent next turn. Treating it as settled would freeze a gap in Cofferdam's own evidence into a
permanent property of the user's work.

**So the rule is symmetric:** none of `met`, `not_met` or `unverified` may be reused as a current
answer without independent current evidence. Where such evidence does not exist, the current status
is **unavailable/unverified** — never a reused stale value, and never inferred preservation.
Stored records are not deleted or invalidated by this; they remain exactly what they always were,
true of their own turn.

## D-2026-08-16-18 — Cofferdam cannot currently prove preservation, and that is what blocks the aggregate (EFE DECISION, ACTIVE)

**Decision.** The current evidence architecture can sometimes prove that a later change happened at a
path. It can **never** prove that one did not. Since cross-turn acceptance requires exactly the
second, the per-turn aggregate cannot be implemented on today's primitives, and no amount of care in
the aggregator repairs that.

**Three independent reasons, each sufficient on its own.**

1. **Cofferdam observes only inside turn windows.** Between one turn's post-work observation and the
   next turn's pre-work baseline — and after the last turn, up to the moment somebody reads — the
   repository is unobserved. A human edit, a rebase, an external tool or another agent leaves no
   trace in any bundle.
2. **Both observation domains are diffs, not state.** `worktree` records paths that differ from HEAD;
   `committed_range` records paths changed within the range. Neither enumerates what exists, so
   "does `foo.py` exist now" has no answer anywhere in stored evidence.
3. **Absence already cannot be read inside a single window** whenever attribution is inexact, the
   pre-work boundary was dirty, or coverage was incomplete — `evaluation._closure` refuses that
   reasoning today, for reasons that only get stronger across several turns.

**One inter-turn check is available now and costs nothing**, recorded so it is not rediscovered:
a turn's `CommittedRangeSummary.target_revision` and the following turn's `GitBaseline.head_revision`
are both already stored, so **committed drift between turns is detectable by comparing two stored
strings**, and the next turn's `working_tree_state` says whether the tree matched HEAD at that
instant. This proves only that a gap is non-empty, never what happened in it — which is enough to
answer `unavailable` honestly rather than leaving a silent hole, and not enough to certify anything.

**What is actually required, in dependency order.**

1. A **final-state evidence surface**: does this path exist at this revision. A different Git question
   (`ls-tree`/`cat-file -e`) from every question PR3–PR5 ask, and therefore a genuinely new evidence
   primitive rather than a re-read of stored rows.
2. **Final-state predicates** — conceptually `path_exists` / `path_absent` — which are re-evaluable at
   any turn because they describe the project rather than a worker's behaviour.
3. Only then a **cross-turn binding layer**.

**Do not weaken exact-turn evidence to get there.** PR2 and PR7 made exact turn bounds load-bearing
deliberately, and `EvidenceBundle` v3 must not be reinterpreted to make aggregation convenient.
Anything cross-turn is a **new derived layer over** the per-turn bundles, never a loosening of them.

## D-2026-08-16-19 — Cross-turn acceptance is its own versioned derived layer, and it is not built yet (EFE DECISION, ACTIVE)

**Decision.** Between the lineage resolver and any future aggregate there is a **missing layer**: for
each criterion active at target turn N, what is its status *at N*, and what evidence supports that.
It is the right shape, it is deliberately not built, and building it before D-2026-08-16-18's
primitives exist would produce a layer that answers `unavailable` for essentially every inherited
criterion — correct, and useless.

**Conceptual shape**, recorded so the eventual implementation is reviewed against a written intent
rather than invented under deadline. Per active criterion at the target turn: the criterion identity;
the target turn; the origin turn and snapshot; a current result of `met` / `not_met` / `unverified`;
the identity of the evidence or evaluation supporting **that** result; and a provenance marker saying
whether it was *newly evaluated at this turn*, *carried under an explicit rule*, *invalidated by later
evidence* or *unavailable*. That provenance field is the point of the whole layer: an answer that
cannot say why it believes itself is not auditable.

**Its own semantic version, in code, when it exists.** Not `RESOLVER_VERSION` (which requirements are
live), not `EVALUATOR_VERSION` (how one criterion is decided against one bundle), not a future
`AGGREGATOR_VERSION` (what a mixture of results means). Binding a result to a turn across a lineage is
a fourth distinct operation and will change for its own reasons. **No such constant is added by this
documentation PR**, in executable code or anywhere else.

**Derived, not persisted — provisionally.** Every input is immutable and the composition is
deterministic, so the same argument that made PR11 derived applies. The one thing that could overturn
it is D-2026-08-16-18's first requirement: a final-state observation is a **new observation**, and
observations are persisted facts. So the *binding* stays derived while the *final-state evidence* it
reads will need storage of its own. That is a schema question for the PR that introduces it, not now.

**What must be true before a runtime per-turn aggregate may be built.** Every criterion active at the
target turn must have a well-defined **current** binding. PR9's ordered rule — any `not_met`
dominates, any `unverified` yields `incomplete`, only all-met yields `met` — remains valid doctrine
and is not in question; its **inputs** are what do not exist. Concretely the aggregate is unblocked
only when either every active criterion originates at the target turn, or final-state predicates make
inherited criteria answerable.

**A narrowing deliberately not taken.** For a `root` or `replace` turn every active criterion
originates at that turn, so PR7's evaluation already *is* a legitimate current binding and PR9's rule
would be sound for those turns. This is true and it is not being shipped: an aggregate whose
correctness silently depends on a lineage shape the caller does not control is a trap, and the first
`extend` would break it without any error.

**`replace` cuts evaluation history exactly where it cuts lineage.** Criteria from before a `replace`
are not active after it, so no cross-turn binding is ever required for them and no evidence traversal
needs to cross that point. The resolver's walk already stops there, so the two layers agree **by
construction** rather than by coordination — which also bounds how far back a future binding layer
could ever need to look. For `revise`, survivors need the cross-turn rule, newly added criteria bind
directly to the target turn, and superseded criteria leave the active set: their stored evaluations
stay true of their own turns and must never contribute to a current answer. Where the resolver already
answers *unavailable* — `not_declared`, `legacy_unknown`, malformed lineage — acceptance is
unavailable too, and no composition may be attempted through unknown continuity.

**Named checks are one of the two roads out, not a detour.** A host-owned named check ("the tests pass")
is inherently a **current-run** property: it is answered by running it now, so it is final-state by
nature and re-evaluable at any turn without any of the problems above. It solves the cross-turn
problem for the criteria that use it and solves nothing for path criteria. Its trust boundary is
unchanged and still binding (D-2026-08-11-7).

**Recorded debt, not fixed here.** `TaskService.send_followup` translates a store-level
`ContinuityInvalid` into `ContinuityUnrecorded`, so a caller's *invalid declaration* is reported as
*could not be recorded*. Harmless today because no caller supplies declarations; it must be fixed
before a real planner or user-facing caller begins submitting them, or the first genuine mistake will
be reported as an infrastructure failure.

## D-2026-08-16-20 — Effective post-worker state is the working tree, and HEAD is only an anchor (EFE DECISION, ACTIVE)

**Decision.** A final-state observation records the state of a bounded set of paths **on the working
tree filesystem**, under the authoritative project root, at the post-worker observation boundary. Not
the committed HEAD tree, and not the index.

**Why not HEAD.** A worker that deletes `foo.py` without committing has left a project in which
`foo.py` is gone; a HEAD-only probe would call it present. A worker that creates `bar.py` without
committing has left a project in which it exists; a HEAD-only probe would call it absent. Both are
wrong about the thing anybody actually cares about — what the project *is* now — and the mistake runs
in both directions, so no amount of care downstream repairs it.

**Why not the index.** `git rm --cached foo.py` empties the index and leaves the file on disk.
"Does this path exist in the effective project workspace" is answered by the filesystem; the index is
a staging intention, and recording it as state would let a plan masquerade as a fact.

**HEAD is still recorded, as an audit anchor.** It says which committed revision the observation sat
alongside, which is genuine context. It is never the authority for existence, and a worktree result
that disagrees with it is not a contradiction: the two describe different things. Both directions are
pinned by tests, including the `git rm --cached` divergence.

**Path state only, in v1.** `present` / `absent` / `unavailable`, plus a bounded kind of `file` /
`directory` / `symlink` / `other` for a present path. **No content, digest, size, mtime, permissions
or directory listing** — a path-state row carrying content would be a second artifact surface
arriving without its own review, and every one of those fields is a way for project text to reach a
database with no need of it.

**`absent` is a positive machine observation** — the safe anchored lookup completed and determined
nothing is there. An IO error, a permission refusal and a refused symlink traversal are therefore
`unavailable` with a closed reason, never `absent`. Collapsing "we could not look" into "it is not
there" is the single most damaging thing this surface could do, because a future acceptance layer
would read the second as evidence.

**Containment is the kernel's, and one detail is load-bearing.** The verified root is opened, then
each component relative to the descriptor above it with `O_NOFOLLOW`. With that flag an intermediate
**directory symlink** reports `ENOTDIR`, not `ELOOP`, which is indistinguishable from "a regular file
where a directory was expected" — so the blocked component is `lstat`-ed to tell them apart. Without
that step `repo/external -> /outside` with target `external/x` would have been recorded as `absent`,
which is exactly the false negative above. An intermediate symlink is refused **even when it points
inside the project**: the rule is about traversal, because a link that is safe today can be repointed
tomorrow. A *final*-component symlink is observed as itself without being followed, so a broken
symlink is a `present` `symlink` rather than an absent path.

**Targets are the resolved active criteria's paths**, `path` and `to_path`, deduplicated by exact
equality only and in the resolver's deterministic order. Never the whole repository — that would be
an unbounded read of somebody's project at every turn boundary. Where the lineage is unavailable
there is no defensible target set and the observation says so; substituting the current snapshot
would be a guessed requirement set wearing an observation's clothes. A resolved **empty** active set
is a complete observation of nothing, and means nothing about acceptance.

**Nothing is reinterpreted.** `path_operation(foo.py, created)` asks what the worker did; observing
that `foo.py` is present does not satisfy it. No new predicate is added, `EVALUATOR_VERSION` stays 1,
`ASSEMBLER_VERSION` stays 3, and `EvidenceBundle` v3 does not carry final state — PR7 stays
turn-local, and a state predicate is its own PR.

## D-2026-08-16-21 — The observation is taken once at the turn boundary and never re-derived (EFE DECISION, ACTIVE)

**Decision.** The final-state observation happens after the worker returns, after PR5's
committed-range observation, and **before the turn is durably closed** — all inside the dispatch lock
— and the result is stored. A read returns the stored row and touches nothing.

**Why reading may never probe.** If a read meant *go and look now*, then repository drift would
silently change historical answers, an audit could not be reproduced, and a remote read would become
a live probe of somebody's filesystem. Proven rather than promised: deleting the project after
capture changes no stored answer, and the read path is asserted not to invoke the observer at all.

**Why that exact position.** The turn row must already exist, so the fact has somewhere to belong;
the turn must not yet be closed, so the observation describes the boundary rather than something
seen afterwards. Pinned by a test that inspects the turn row from inside the write, and by one that
records the call order.

**Boundary loss is never repaired.** A process that dies before the observation leaves no row, and
the read answers `legacy_unknown`. That state deliberately does not distinguish "predates PR14" from
"the boundary was lost", because they mean the same thing to every consumer — nothing was recorded,
so nothing may be assumed. The third option, looking at the filesystem now and filing the answer
under a turn that ended long ago, is a statement about today wearing yesterday's timestamp, and no
recovery path may do it. The migration is held to the same rule: **no backfill**, no project opened,
no Git run, no observer called.

**Complete, incomplete, unavailable — and partial is never called whole.** One unobservable path
makes the observation `incomplete`; the paths that *were* observed are still stored, because they are
real facts worth auditing, and the state says plainly that no consumer may treat it as complete.
`unavailable` carries no paths at all, because there was no defensible target list.

**Bounded, and honest about what the bound buys.** At most 256 target paths — chosen for filesystem
work at a turn boundary, not derived from PR6's per-snapshot limit, because an active set accumulates
across turns — and refused rather than truncated. The set is read twice with bounded retries and
refused as `observation_unstable` if it will not settle; no optimistic result follows detected
instability. The limitation is stated rather than implied: v1 observes existence and kind, so a file
whose *contents* changed between passes looks identical to both, and a content-level guarantee would
need content evidence this PR deliberately does not collect.

**An observation failure never rewrites the task.** A project that could not be read is a gap in
Cofferdam's evidence, not a fault in the user's work. A completed worker stays completed and the gap
is recorded as an explicitly unavailable observation — the same rule the Git baseline and the
committed range already follow.

**`FINAL_STATE_OBSERVER_VERSION = 1`**, code-owned and distinct from `SCHEMA_VERSION`,
`CRITERIA_MODEL_VERSION`, `CONTINUITY_MODEL_VERSION`, `ASSEMBLER_VERSION`, `EVALUATOR_VERSION`,
`RESOLVER_VERSION` and the future binding and aggregate versions. Bound into a deterministic
observation fingerprint over the observer version, the target, the state and limitation, the lineage
fingerprint that selected the paths, the HEAD anchor and every path result in stored order — and not
over any clock, minted id, rowid or host path.

## D-2026-08-17-1 — A PR7 evaluation means one turn's own snapshot against that turn's own bundle, and that meaning is frozen (EFE DECISION, ACTIVE)

**Decision.** A stored `task_turn_evaluations` row means exactly:

> the judgement of **turn N's own immutable criteria snapshot**, against **turn N's own exact
> `EvidenceBundle`**, under `EVALUATOR_VERSION` 1.

This was verified against the merged code rather than assumed. `_evaluate_one_turn` reads
`turn_criteria(task_id, turn_number)` and `evidence_bundle(task_id, turn_number)` — the same turn
both times. `record_evaluation` takes a single `CriteriaSnapshot` and derives `task_id`,
`turn_number`, `criteria_snapshot_id` and `criteria_fingerprint` from it, and refuses unless the
result count equals that snapshot's criterion count. `evaluation_fingerprint` binds one turn
identity, one snapshot identity and one bundle identity. **Origin turn and target turn are the same
number, and nothing in the row distinguishes them** — because until now they could not differ.

**This identity must never be silently widened to "all criteria active at turn N".** Every stored
row was written under the narrow meaning. Widening it would not add information; it would retroactively
change what thousands of existing rows claim, and no reader could tell which meaning any given row
was written under, because nothing is stored that would say. A widened reading also cannot be
reconciled with the `result_count = criterion_count` invariant, which is what stops "no criteria"
from ever totalling up as "everything passed".

**The schema does not defend this meaning — the write path does.** Probed directly against a real
v10 database: `task_turn_criterion_results` has a foreign key to `task_turn_criterion_items`
(criterion exists *somewhere*) and **no** constraint tying a result's criterion to its evaluation's
`criteria_snapshot_id` or turn. Inserting turn 1's criterion into turn 2's evaluation is **permitted
by the DDL**, as is an evaluation whose `turn_number` and `criteria_snapshot_id` belong to different
turns. Both are refused only by `record_evaluation`'s snapshot-driven API. That is not a bug to fix
here, but it is the reason the honest meaning cannot be preserved by adding rows to this table: the
database would not stop a dishonest one.

**`UNIQUE (task_id, turn_number, evaluator_version)` is the binding structural fact.** One target
turn admits exactly **one** evaluation per evaluator version. Two evaluation semantics — turn-change
and current-state — therefore cannot coexist for one turn without either bumping `EVALUATOR_VERSION`
to mean "a different kind of question" (which it does not mean) or storing them apart.

## D-2026-08-17-2 — Current-state assessment is a separate layer with its own identity, not an extension of the EvaluationRecord (EFE DECISION, ACTIVE)

**Decision.** Results derived from a PR14 `FinalStateObservation` are recorded in their **own**
layer, keyed by target turn and criterion. `task_turn_evaluations` and
`task_turn_criterion_results` keep their PR7 meaning unchanged, forever.

**Why not extend the existing record (Option A).** It fails on four counts, in ascending order of
seriousness. Historical rows would need a nullable evidence-domain column whose `NULL` silently means
"change" — the ambiguous nullability that makes a schema unreadable. The parent row would carry two
input fingerprints, only one of which any given child used, so provenance would have to be restated
per result anyway. The uniqueness key admits one evaluation per turn per evaluator version, so the
two domains would share a parent whose `criteria_snapshot_id` and `criteria_fingerprint` could only
describe one of them. And an inherited criterion answered inside a turn's evaluation would break the
`result_count = criterion_count` invariant, which is load-bearing. Extending is not cheaper; it is
the same work done where it cannot be constrained.

**Why not a generalised `EvaluationInput` framework (Option C), yet.** There is exactly **one** real
second domain today. A general input model designed against one real case and two imagined ones
would be designed against imagination, and this milestone has refused that at every step. But the
door is left open deliberately: the new layer carries an explicit **evidence-domain discriminator**,
so a future named-check result — a bounded host-owned execution, which is neither turn-change
evidence nor a path observation — joins as a third domain value rather than a fourth table.

**Result vocabulary is reused unchanged**: `met`, `not_met`, `unverified`. No confidence, no score,
no probability, no task verdict. An inability to obtain a trustworthy current answer is
`unverified` with a closed reason, never `not_met`.

**Origin turn and target turn are separate columns and must never be collapsed.** A criterion
introduced at turn 1 and assessed at turn 4 has two honest identities, and a single `turn_number`
could only lie about one of them. PR11's `ActiveCriterion` already carries `source_snapshot_id` and
`source_turn_number`; the layer persists both alongside the target turn, and the pair
`(target_turn, criterion_id)` is the natural key.

**Every target-turn assessment is retained.** A criterion assessed `met` at turn 1, `met` at 2,
`not_met` at 3 and `met` at 4 leaves four immutable rows, because those are four machine judgements
made at four different world boundaries. There is **no** mutable "current status" row: overwriting
would destroy the only evidence that something broke and was fixed, which is precisely the history an
audit exists to show. "Current" means *at the target turn*, not *latest*.

## D-2026-08-17-3 — A state result may not exist without the exact immutable observation it came from (EFE DECISION, ACTIVE)

**Decision.** A current-state result must bind, as **authority**:

* the **target turn**, and the **criterion id**;
* the **final-state observation fingerprint** — the content identity of the exact observation read;
* `FINAL_STATE_OBSERVER_VERSION` — because *what `present` means* is that version's semantics, and a
  version 2 with a different authority or symlink rule would make the same word a different claim;
* the **resolved active-lineage fingerprint**, which proves the criterion was **active** at the
  target turn rather than merely existing in history;
* the **current-assessment semantic version**, which maps the two together.

And as **redundant audit context**, denormalised for legibility but never relied on: the criterion's
origin turn and snapshot (derivable from the criterion row), the observation id (derivable from the
target turn, since the observation is keyed one-per-turn), and the HEAD anchor (already inside the
observation).

**The lineage fingerprint is bound explicitly even though PR14's observation already carries one.**
They are the same value today, and binding both is nearly free; but the observation's copy answers
*why those paths were looked at*, while the assessment's answers *why this criterion counts here*.
Deriving one from the other would tie two questions together that a later change could separate.

**`EvidenceBundle` v3 is not the vehicle, and must not become one.** Stuffing a `FinalStateObservation`
into the bundle to reuse the existing record would reinterpret `ASSEMBLER_VERSION`, change what every
stored `evidence_input_fingerprint` refers to, and merge two evidence meanings — turn-local change
and effective resulting state — that PR13 and PR14 spent two PRs separating. If a composite input
object is ever wanted, it is a new layer with a new version, not a widened bundle.

**Observation completeness maps to results per path, not per observation.** PR14 stores a state for
every target path, so `incomplete` does not poison the paths that *were* observed: a path row reading
`present` or `absent` is individually complete and authoritative. The rule is therefore *per path*:
a path row of `present`/`absent` is usable; a path row of `unavailable`, a path with no row, an
`unavailable` observation and a `legacy_unknown` turn all yield **`unverified`**. A missing
observation is never `not_met` — absence of evidence is the one thing this whole milestone refuses to
read as evidence of absence.

## D-2026-08-17-4 — State predicates are authored explicitly and never derived from action criteria (EFE DECISION, ACTIVE)

**Decision.** `path_exists(P)` and `path_absent(P)` — when they exist — are written by whoever states
the requirement. Cofferdam never manufactures one.

**Never**: `path_operation(P, created)` does not become `path_exists(P)`, and
`path_operation(P, deleted)` does not become `path_absent(P)`. The first asks what a worker did in one
turn; the second asks what is true at a boundary. A requirement to *create* a file is satisfied by
creating it, and is silent about whether it must still be there in nine turns' time — inventing that
second requirement would enforce something nobody asked for. **Continuity may not perform this
transformation either**, in any mode: a lineage relation carries requirements forward as they were
written, and a mode that rewrote a predicate on the way through would be inferring intent, which is
the thing declared lineage exists to avoid.

**PR14's observation scope does not imply state-evaluability.** PR14 selects target paths from the
active criteria's `path`/`to_path`, which today are all change criteria. That is sound as machine
evidence — the paths are worth observing regardless. It does **not** mean those change criteria can
be answered from the observation, and the future state evaluator must select by **predicate
semantics**, never by "a path was observed".

**Adding the predicates is not additive, and this is the concrete blocker.** `task_turn_criterion_items`
constrains `predicate` with `CHECK (predicate IS NULL OR predicate IN ('path_changed',
'path_operation', 'rename'))` — verified by attempting the insert, which SQLite refuses. SQLite cannot
alter a CHECK constraint, so admitting a new predicate requires a **full table rebuild** of a table
holding immutable historical criteria and referenced by a foreign key from
`task_turn_criterion_results`. Every schema step in this project so far has been a pure
`CREATE TABLE IF NOT EXISTS`; this would be the first destructive-shape migration, and it must be
treated as one — rehearsed, backed up, and rolled back as a pair.

## D-2026-08-17-5 — An inherited change criterion is `unverified`, and that is the accurate answer (EFE DECISION, ACTIVE)

**Decision.** At target turn N, an active criterion whose predicate is
`path_changed`, `path_operation` or `rename` and whose **origin turn is earlier than N** is
`unverified`, with the closed reason `inherited_change_not_current_state_evaluable`. The origin
turn's stored result is not reused, no re-evaluation is attempted, and PR14's final-state
observation is not consulted.

**All three alternatives are wrong, and PR13 showed why.** *Carrying the old result forward* reuses a
statement about turn 1 as a statement about turn 4: it misses later breakage when it was `met` and
later repair when it was `not_met`, and the record cannot say which happened. *Re-evaluating against
the target turn's evidence* asks "did **this** turn create `foo.py`?" of a requirement satisfied three
turns ago and correctly left alone since — the honest answer is *no*, and reporting it as `not_met`
would fail work precisely when it was right. *Reading final state* — `foo.py` is present, so
`created` is met — is the semantic conversion D-2026-08-17-4 forbids.

**So `unverified` is not a placeholder.** It is the accurate answer: Cofferdam has no evidence of the
right kind at this boundary, and says so. Pinned in all three directions — an origin `met`, an origin
`not_met` and an origin `unverified` produce not merely equal results but **identical assessment
fingerprints**, so the origin cannot be recovered from the current answer by any consumer.

**Contrast, and it is the whole shape of V1.** A criterion whose origin turn **is** the target turn
and whose predicate is a change predicate binds to PR7's stored judgement for that turn — read, never
recomputed, with the PR7 `evaluation_fingerprint` carried as provenance. A **manual** criterion is
`unverified` wherever it came from, under its own reason, because no machine authority exists for it
at any turn and this build has no human-answer channel.

**Only PR11's resolved active set is assessed.** Never all historical criteria, never the latest
snapshot, never an accumulation. Superseded criteria and criteria cut by a `replace` are **absent**
from the answer rather than present-and-unverified, because they are not required here. Where the
lineage is unavailable the whole set is unavailable; there is no partial answer, which a caller would
have every incentive to use.

## D-2026-08-17-6 — The current assessment is derived, and its refusals are separated by kind (EFE DECISION, ACTIVE)

**Decision.** The layer adds **no table and no schema change**; schema stays at v10. Every input —
the criterion row, the continuity declaration, the PR7 evaluation — is immutable and versioned, so
the answer is a pure function that re-derives identically forever. Persisting it would add a write
path, a recovery path, and a second place for the truth to live that could disagree with the first.

**`CURRENT_ASSESSMENT_VERSION = 1`**, code-owned and distinct from `SCHEMA_VERSION`,
`CRITERIA_MODEL_VERSION`, `CONTINUITY_MODEL_VERSION`, `ASSEMBLER_VERSION`, `EVALUATOR_VERSION`,
`RESOLVER_VERSION`, `FINAL_STATE_OBSERVER_VERSION` and the future aggregate version. It owns exactly
the mapping *active criterion + evidence domain → current result at a target turn*, and nothing about
how any underlying judgement was reached.

**Four refusals, deliberately not collapsed into one**, because they are different kinds of silence
and only some of them change by waiting:

| Refusal | Means |
| --- | --- |
| `turn_not_closed` | the target is not a completed boundary; a current assessment of a running turn describes a moment that has not happened |
| `lineage_unavailable` | PR11 cannot determine the active set; there is no requirement set to assess |
| `evaluation_not_recorded` | **operational** — the turn is closed and PR7 simply has not run yet |
| `evaluation_inconsistent` / `unsupported_evaluator_version` | a stored row violates the service-owned invariants, or was written by evaluator semantics this binder does not know |

**`evaluation_not_recorded` is set-level on purpose.** Reporting a pending recovery pass as a set of
`unverified` criteria would file a gap in Cofferdam's own pipeline as a statement about the user's
work. It is also never `not_met`: absence of evidence is not evidence of absence, here as everywhere.

**A turn needing no PR7 record does not wait for one.** If every active criterion is inherited or
manual, nothing at the target turn could be answered by an evaluation, and demanding one would make a
complete answer wait on a record nothing would read.

**Stored PR7 rows are validated, not trusted.** D-2026-08-17-1 established that the DDL permits
several dishonest combinations — a result naming a criterion outside its snapshot, an evaluation whose
turn and snapshot disagree. The binder checks task, turn, snapshot identity, declared count against
carried results, duplicate answers, and that every same-turn criterion was actually answered. It
**fails closed and repairs nothing**: a read that fixed a row would destroy the evidence that
something wrote it.

**Supported evaluator versions are enumerated, not assumed.** A future `EVALUATOR_VERSION` 2 may
decide criteria differently, and binding its results as though they meant version 1's thing is exactly
the silent reinterpretation this layer exists to prevent — so an unrecognised version is refused
under its own reason, distinct from "no evaluation".

**One pinned read snapshot.** Lineage and the evaluation are fetched inside a single deferred read
transaction. This matters more than it did for PR11: an evaluation row is **not** frozen at dispatch —
bounded recovery writes it later — so an active set read before that commit combined with an
evaluation read after it would describe a database state that never existed.

**Internal only.** No HTTP route, no bridge Action, no PWA control, and PR8's assessment response is
unchanged. **No aggregate**, no verdict, no `AGGREGATOR_VERSION`: this produces the legitimate
per-criterion inputs an aggregate would need and stops there.

## D-2026-08-17-7 — The criteria vocabulary is widened by rebuilding the table, and that is the first destructive migration here (EFE DECISION, ACTIVE)

**Decision.** Schema **v11** admits two state predicates — `path_exists` and `path_absent` — into
`task_turn_criterion_items`. Because SQLite has no `ALTER TABLE ... DROP CONSTRAINT` and the
predicate list is enumerated in a `CHECK`, the only way to widen it is to build a new table and move
the rows. Verified rather than assumed: both `ALTER TABLE ... ADD/DROP CONSTRAINT` forms are a syntax
error, and the v10 `CHECK` refuses the insert.

**The intentional delta is exactly one clause.** The eleven other checks already constrain the new
predicates correctly and needed no change: a state predicate is not `path_operation`, so `operation`
must be NULL; it is not `rename`, so `to_path` must be NULL; it is an evidence kind, so `path` is
required. Nothing else about the table moves.

**Foreign keys are the load-bearing risk, and they are disabled deliberately.** Three keys point at
this table: `task_turn_criterion_results` (`CASCADE`) and both sides of
`task_turn_criterion_supersessions` (`CASCADE` and `RESTRICT`). Measured, not reasoned about: with
enforcement on, `DROP TABLE` is **refused** by the `RESTRICT` side; with it off, the child rows
survive untouched. Enforcement is therefore suspended for the rebuild, **outside** the transaction —
`PRAGMA foreign_keys` is a no-op inside one, which was confirmed empirically rather than trusted —
restored in a `finally`, and the restoration is **verified**, because a connection silently running
without enforcement would be a worse outcome than a failed migration.
`PRAGMA foreign_key_check` runs inside the transaction before the commit, so a rebuild that orphaned
anything rolls back instead of committing.

**Build-aside-and-rename, not rename-aside-and-build.** Modern SQLite rewrites `REFERENCES` clauses
in other tables when a table is renamed, so renaming the *old* table out of the way would repoint all
three foreign keys at the doomed table. Renaming the *new* table in is inert, because nothing
references its temporary name. The one artifact is cosmetic and is recorded so it is never mistaken
for drift: `ALTER TABLE ... RENAME TO` stores the name **quoted**, so a migrated database reads
`CREATE TABLE "task_turn_criterion_items"` where a fresh one reads it unquoted. A test asserts that
the quoting is the *only* difference between fresh and migrated — every column, constraint and index
is byte-identical.

**Completion is detected from the stored DDL, not the version number.** A crash after the rename but
before the version row is updated leaves a database whose shape is already correct; detecting by DDL
makes the next open a no-op rather than a second rebuild. The version bump is deliberately the last
step for the same reason.

**No backfill and no conversion, ever.** `path_operation(P, created)` remains exactly that; no row
was rewritten to use the new words, and none ever will be. Pinned by a test that reads the historical
predicates back after migration.

## D-2026-08-17-8 — State predicates are representable before they are evaluatable, and that is safe by prior design (EFE DECISION, ACTIVE)

**Decision.** PR17 ships the vocabulary without any evaluation of it. A `path_exists` criterion can be
authored, validated, stored, fingerprinted, resolved, inherited and superseded, and **nothing decides
it**.

**This was the gate that could have stopped the PR, and it resolves affirmatively because both
deciding layers were already built total.** PR7's evaluator dispatches on a predicate table and
returns `unverified` with `unsupported_capability` for anything absent from it — the seat its author
described as "where a future capability will sit". PR16's binder returns `unverified` with
`unsupported_predicate` for any predicate outside its change set; that branch was written and tested
with `path_exists` literally, before the predicate existed. So the lifecycle was verified end to end
rather than argued: the turn evaluates, the record is complete and valid with the right result count,
the turn closes normally, and the current assessment resolves — with no crash, no dropped criterion,
no invalid `EvaluationRecord`, and **no `met` or `not_met` anywhere**.

Had either layer been partial rather than total, the correct answer would have been to merge the
vocabulary and the final-state binder atomically. It was not necessary, and forcing them together
would have made the first destructive migration land in the same PR as new evaluation semantics.

**PR14 picks the path up, and that means nothing about acceptance.** A state criterion contributes
its `path` to the bounded final-state observation scope exactly as a change criterion does, so the
observer may record `present` / `absent` / `unavailable` for it. That is a *representation*
consequence of how targets are selected, not an interpretation: the observer never sees a predicate,
and no acceptance result is produced from what it records.

**No version moved but the schema.** `EVALUATOR_VERSION` stays 1, `CURRENT_ASSESSMENT_VERSION` stays
1, `FINAL_STATE_OBSERVER_VERSION` stays 1, `CRITERIA_MODEL_VERSION` stays 1 — the existing criteria
fingerprint already binds predicate and path honestly, so `path_exists(foo.py)` and
`path_absent(foo.py)` hash differently without any new fingerprint version, and neither hashes like
the `path_operation` criterion it superficially resembles.

**Rollback is a pair, and it stops being clean the moment v11 is written to.** A slot flip alone
cannot walk a schema backwards, so a rollback needs the old runtime **and** a verified pre-v11
backup. Before any v11-only criterion exists, restoring that backup is a clean point-in-time
downgrade. **After** a `path_exists` or `path_absent` criterion has been written, restoring it
destroys requirements a user actually stated — the old schema cannot represent them, so there is no
lossless path. Those two cases must never both be described as "simple rollback".

## D-2026-08-17-9 — `final_state` is a second evidence domain, and the assessment version moves with it (EFE DECISION, ACTIVE)

**Decision.** PR18 teaches the derived current-assessment layer to answer `path_exists` and
`path_absent` from the target turn's immutable `FinalStateObservation`, and moves
`CURRENT_ASSESSMENT_VERSION` from **1 to 2**.

**The version had to move, and the reason is not cosmetic.** The shape of an assessment did not
change; its *meaning* did. A criterion V1 answered `unverified` / `unsupported_predicate` can now be
`met` or `not_met`, and the closed domain vocabulary gained a member. That is exactly what this
number owns — *active criterion + evidence domain → current result at a target turn* — so a V1
fingerprint and a V2 fingerprint of the same criterion must not collide. They are answers to the same
question as two different builds understood it, and a reader must be able to tell which.

**Nothing else moved.** `SCHEMA_VERSION` stays 11 with no migration, `EVALUATOR_VERSION` stays 1,
`FINAL_STATE_OBSERVER_VERSION` stays 1, `RESOLVER_VERSION`, `CONTINUITY_MODEL_VERSION`,
`CRITERIA_MODEL_VERSION` and `ASSEMBLER_VERSION` are untouched, and there is no `AGGREGATOR_VERSION`.
PR18 is derived read semantics: no table, no write path, no recovery path.

**The domain vocabulary is now `turn_change` / `final_state` / `not_applicable`, and it is still
closed.** `named_check` is named and not implemented. Every assessment binds the domain it used into
its fingerprint, which is what stops a later domain from silently reinterpreting an older answer.

**PR7's state-predicate row stays and is not authority.** PR7 records `path_exists` as `unverified` /
`unsupported_capability`. That is a correct, permanent statement about what the turn-change evaluator
could establish, and PR18 neither reads it for a state criterion nor rewrites it. Pinned mechanically
rather than argued: the stored PR7 result is varied across `met`, `not_met` and `unverified`, and the
state answer does not move. This is the concrete payoff of D-2026-08-17-2 keeping the two layers
apart.

## D-2026-08-17-10 — A state criterion is answered at its target turn, and never carried forward (EFE DECISION, ACTIVE)

**Decision.** A `path_exists` or `path_absent` criterion is decided by the **target** turn's stored
observation, whether it originated at that turn or five turns earlier. No previous target's answer is
reused.

**Why this is the opposite of D-2026-08-17-5, and consistent with it.** An inherited *change*
criterion is `unverified` because its question — *what did the worker do during turn 1* — is not a
question about turn 4, and no evidence answers it there. An inherited *state* criterion's question —
*is `foo.py` there* — is exactly as meaningful at turn 4, and turn 4's own boundary answers it. The
rule was never "inherited means unknown"; it was always "evidence must match the criterion's
semantics", and state predicates are the first criteria whose semantics reach forward.

So the same criterion legitimately reads `met` at turn 1, `not_met` at turn 2 after a deletion, and
`met` again at turn 3 after a repair. Three derived facts about three boundaries, with three distinct
fingerprints, none persisted and all recomputable forever from immutable rows.

**Object kind does not enter existence.** *Any* filesystem object counts as `present`: a file, a
directory, a symlink, a **broken** symlink, a socket. PR14 records the link object itself without
following it and PR18 does not follow it either, so `path_exists(link)` is `met` and
`path_absent(link)` is `not_met` for a link pointing nowhere. No `path_is_file`, `path_is_directory`
or `path_is_symlink` predicate was added.

**A missing path row is never `absent`.** PR14 gives every target an explicit child row and stores an
unobservable path as `unavailable` with a reason, so absence of a row means the observation does not
describe the scope it claims — a structural defect, not a missing file.

## D-2026-08-17-11 — Evidence is required only by the criteria that consume it, and corruption fails the set closed (EFE DECISION, ACTIVE)

**Decision.** Two rules that had to be settled together, because each is the other's failure mode.

**Input dependency is domain-conditional, in both directions.** A PR7 `EvaluationRecord` is required
exactly when some active criterion originated at the target turn *and* is a change predicate — never
merely because a target turn exists. A `FinalStateObservation` is required exactly when some active
criterion is a state predicate, at any origin — never merely because PR14 recorded one. A target
whose active set is one `path_exists` resolves with no evaluation at all; a target of change and
manual criteria has no dependency on PR14 whatsoever, proven by handing it a structurally broken
observation and getting an identical fingerprint. Making unused evidence an authority dependency
would let a lag in one pipeline stage block answers that never needed it.

**Semantic limitation and structural corruption are separated, and must stay separated.** A path row
recorded `unavailable`, an observation legitimately `unavailable`, and a turn with no observation at
all are things Cofferdam *observed and stands behind*: each maps its criterion to `unverified` with a
closed reason, never to `not_met`, and the set still resolves. An unknown observer version, a wrong
task or turn identity, a `path_count` that disagrees with its children, a duplicated path, an
observation whose fields do not hash to its stored fingerprint, a lineage fingerprint that disagrees
with the active set resolved now, or an expected path missing from a claimed scope mean the row **is
not what the service writes** — and each fails the whole set closed, with nothing repaired.
Laundering the second kind into the first would file tampering as a routine limitation.

**Lineage agreement is a set-level gate, not a per-criterion one.** PR14 chose its targets from the
active lineage at capture time and PR11 resolves it again at read time; a disagreement means the
observation's declared scope belongs to a different requirement set, so no individual path row from
it may be consumed however right its name looks. A scope-identity mismatch is not a fact about any
single criterion.

**Stored fingerprints are verified, not trusted.** PR14 wrote `observation_fingerprint` and nothing
read it back, so a raw-SQL edit to a path state, kind or reason would have been consumed as authority
on the strength of a string nobody recomputed. PR18 adds a verifier **at PR14's layer**, calling
PR14's own `final_state_fingerprint`, so there remains exactly one fingerprint algorithm rather than
two that eventually disagree.

**One coherent read snapshot, and the resolver moved inside it.** Deciding whether a final-state row
is needed requires the resolved active set, so PR11's resolver now runs inside the store's deferred
read transaction alongside the lifecycle, the lineage graph, the optional evaluation and the optional
observation. Deciding what to read from one database state and then reading it from another is
precisely the split the snapshot exists to prevent.

## D-2026-08-17-12 — PR9's two dimensions survive; their inputs move from one snapshot to the resolved active set (EFE DECISION, ACTIVE)

**Decision.** [D-2026-08-16-3](#d-2026-08-16-3--per-turn-assessment-has-two-dimensions-and-known-failure-dominates-uncertainty-efe-decision-active)
is **reconciled, not replaced.** Both dimensions stand exactly as written — availability, then an
acceptance outcome that exists only when assessable — and both the ordered fold and the vocabulary
`met` / `not_met` / `incomplete` / `not_assessable` are unchanged.

What changes is where availability is *read from*. PR9 derived it from the target turn's own criteria
state, which was the only honest source before continuity existed. PR11–PR18 made the resolved
**active** set the real requirement population, so availability is now derived from PR18's
`CurrentAssessment` envelope. The three PR9 rows map onto it without loss of meaning:

| PR9 (criteria state) | PR18 envelope | availability | reason |
| --- | --- | --- | --- |
| `present` | `resolved`, `criterion_count > 0` | `assessable` | — |
| `not_provided` | `resolved`, `criterion_count == 0` | `not_assessable` | `no_structured_criteria` |
| `legacy_unknown` | `unavailable` / `lineage_unavailable` | `not_assessable` | `lineage_unavailable` |

The third row is the only shape change: a historical unknown no longer reaches the aggregate as a
criteria-state reading, because the resolver refuses to produce an active set from it at all and the
binder reports the refusal at the **set** level. Same conclusion, reached one layer earlier.

**Two top-level availability states, not three.** PR18 added eight set-level refusals PR9 never had,
and they divide cleanly into three families by *what a caller should do next* — see
[D-2026-08-17-13](#d-2026-08-17-13--set-unavailable-and-criterion-unverified-are-different-facts-and-the-aggregate-must-keep-them-apart-efe-decision-active).
That division is real and is recorded, but it does **not** earn a third top-level state: in every one
of those families there is no acceptance outcome, which is precisely what `not_assessable` already
says. A third state would make every caller branch three ways to learn something the closed reason
already tells them.

**The aggregate does not translate PR18's reasons; it passes them through.** `availability_reason` is
PR18's `unavailable_reason` verbatim, plus exactly one value PR18 cannot produce —
`no_structured_criteria`, for the resolved-but-empty case. A parallel aggregate vocabulary would be a
second closed set that has to be kept in step with the first, and this repository already carries a
live example of what that costs: the untranslated `ContinuityInvalid` → `ContinuityUnrecorded` debt.
One vocabulary, extended by one member.

**Acceptance outcome remains absent, not null-valued, when not assessable.** A turn whose active set
could not be established is never `incomplete`. `incomplete` is a statement about a *known*
requirement population containing at least one `unverified` criterion, and applying it to a set that
was never determined would report evidence uncertainty where the real problem is that Cofferdam does
not know what was required.

## D-2026-08-17-13 — Set-unavailable and criterion-unverified are different facts, and the aggregate must keep them apart (EFE DECISION, ACTIVE)

**Decision.** The distinction PR18 built into the binder is load-bearing at the aggregate too, and
neither direction of collapse is permitted.

**Criterion-level `unverified`** means *we know which active criterion this is and cannot establish
`met` or `not_met` with sufficient authority.* The requirement population is known and this member of
it is uncertain. It folds normally: it cannot produce `met`, and it yields `incomplete` when no
`not_met` outranks it. Its reasons — `inherited_change_not_current_state_evaluable`,
`manual_criterion_no_machine_authority`, `final_state_path_unavailable`, `final_state_unavailable`,
`final_state_not_recorded`, `unsupported_predicate` — are audit provenance and **must not** create
special acceptance rules.

**Set-level unavailable** means *Cofferdam cannot honestly construct the requirement population at
all.* There is nothing to fold. PR18's nine set reasons group into three families, and the grouping is
documented rather than made a runtime field, because it changes what a caller should *do* and not
what the acceptance answer *is*:

| Family | PR18 reasons | Changes by waiting? |
| --- | --- | --- |
| population unknown | `lineage_unavailable` | no |
| operational | `turn_not_closed`, `evaluation_not_recorded` | **yes** |
| structural integrity | `evaluation_inconsistent`, `unsupported_evaluator_version`, `final_state_inconsistent`, `unsupported_final_state_observer_version`, `final_state_lineage_mismatch`, `final_state_path_missing` | no — needs investigation |

**Rendering any of these as `incomplete` is forbidden.** For the operational family it would file a
lag in Cofferdam's own pipeline as a statement about the user's work; for the structural family it
would hide evidence corruption or tampering inside ordinary evidence uncertainty, which is the exact
laundering PR18 refuses one layer down. Both would also be *stable-looking* answers to *unstable*
situations, which is worse than an explicit refusal.

**Not-met dominance is retained unchanged.** Any active criterion whose current result is `not_met`
makes the outcome `not_met`, however many others are `unverified`. Deterministic machine evidence that
one active requirement is unsatisfied is not erased by an inability to verify another. Confirmed
against the now-real model rather than argued: a `not_met` state criterion beside an `unverified`
inherited change criterion folds to `not_met`, across two different evidence domains.

**Aggregation is domain-agnostic.** The fold reads `result` and nothing else. It must not prefer,
weight or rank `turn_change` against `final_state`, must not treat an observation as stronger than an
evaluation, must not discount inherited-change `unverified`, and must not know anything about Git,
paths or filesystem mechanics. All of that complexity is resolved below it and is already committed to
by the envelope's fingerprint.

## D-2026-08-17-14 — Zero active criteria is `not_assessable`, and it has exactly one meaning (EFE DECISION, ACTIVE)

**Decision.** A resolved active set of size zero is **`not_assessable` / `no_structured_criteria`**.
It is never `met`. Vacuous truth is not acceptance, and "no requirements were stated" must never be
rendered as "every requirement was satisfied" — the second is a claim about work that nobody made.

**The subtle part turned out not to be subtle, and the reason is a real invariant.** The concern was
that several different lineage shapes could produce a zero-count set and the aggregate would be unable
to tell them apart. Checked against the merged resolver and the merged write path rather than reasoned
about:

* `root` with a `not_provided` snapshot → resolves, zero active;
* `replace` with a `not_provided` snapshot → resolves, zero active;
* `extend` over such a chain, itself `not_provided` → resolves, zero active;
* `revise` → **cannot** produce zero. `revise` is the only mode requiring supersession relations, and
  every relation's `criterion_ordinal` must name a criterion of the **current** turn's own snapshot;
  a `not_provided` revise is refused at write with `REASON_RELATION_CURRENT_UNKNOWN`. So a revise
  always contributes at least one criterion and leaves the active set non-empty;
* `extend` never removes anything;
* criteria `legacy_unknown` and continuity `not_declared` never reach a resolved set at all — the
  resolver refuses and the binder reports `lineage_unavailable`.

So **a resolved zero-count active set can only mean that no structured criteria were declared anywhere
in the resolved chain.** There is exactly one meaning, the count alone carries it, and no additional
derived provenance is required for this case. PR9's `not_provided` row is preserved exactly.

**`met` therefore requires a non-empty active set by construction**, not by a guard bolted onto the
fold: the availability dimension answers first, and an empty set never reaches the outcome dimension.

## D-2026-08-17-15 — The target-turn aggregate is derived, pure, and versioned separately (EFE DECISION, ACTIVE)

**Decision.** The shape of the runtime aggregation PR, settled before any of it is written.

**Target-turn only.** The aggregate answers *acceptance at target turn N, over the criteria active at
N, using their current status at N.* It is not a task verdict, not merge readiness, not deployment
readiness and not project quality.
[D-2026-08-16-4](#d-2026-08-16-4--there-is-no-task-level-acceptance-because-criterion-continuity-does-not-exist-yet-efe-decision-rationale-superseded-in-part-by-d-2026-08-17-15)
named the missing fact as criterion continuity, and PR10–PR12 supplied it — so the blocker **on a
target-turn aggregate is removed**. The blocker on a *global task* verdict is not, and is not being
lifted here: composing several target-turn answers into one is a separate question about which turn's
requirements a task is judged against, and nobody has decided it.

**Derived on read, never persisted.** Every input is immutable or deterministically derived from
immutable rows, the fold is pure, and the fingerprint gives audit identity without storage. No table,
no migration, no write path and no recovery path — the same conclusion PR16 reached for the layer
below, for the same reasons.

**Pure, and it consumes exactly one thing.** `aggregate(current_assessment) -> AcceptanceAggregate`.
No SQLite, no filesystem, no Git, no subprocess, no `EvidenceBundle`, no `FinalStateObservation`, no
evaluator, no resolver, no provider and no clock. The service builds the `CurrentAssessment` and hands
it over; verified as achievable rather than hoped for — a throwaway prototype folded every case,
including counts and `requires_human`, from the envelope alone.

**`requires_human` is derived from criterion kind, never from uncertainty.** True when at least one
active criterion is `manual` (or, later, another kind that explicitly needs human authority). An
inherited-change `unverified` is `requires_human = false`; a missing final-state observation is
`requires_human = false`. Deriving it from "any `unverified`" would tell a user to go and look at
something no human can resolve. It is orthogonal context beside the outcome, **never** a fourth
acceptance value — restating
[D-2026-08-16-3](#d-2026-08-16-3--per-turn-assessment-has-two-dimensions-and-known-failure-dominates-uncertainty-efe-decision-active)
against the concrete model.

**Manual criteria still cap the outcome at `incomplete`, with no exception.** A manual criterion is
`unverified` today because no human-answer channel exists, so an active set containing one cannot
reach `met`. This is recorded, not worked around: an exception that ignored manual criteria would let
a turn report `met` while a requirement a person was supposed to check went unchecked.

**The fingerprint composes rather than re-derives.** It binds `AGGREGATOR_VERSION`, the task and
target turn, **`CurrentAssessment.fingerprint`**, the availability and its reason, the outcome, the
counts and `requires_human`. It does **not** re-bind evidence bundles, evaluation records or
final-state observations: the envelope fingerprint already commits to the lineage fingerprint and to
every criterion-level answer, so binding them again would duplicate the commitment and couple the
aggregate to layers it is not allowed to read. No clock, no rowid, no host path, no session id.

**`AGGREGATOR_VERSION = 1`** is recommended for the runtime PR and is deliberately **not** added as an
executable constant here. It owns exactly *`CurrentAssessment` → target-turn availability and
outcome*. A future evidence domain or criterion family that produces the same criterion-level `met` /
`not_met` / `unverified` needs **no** bump, because the fold does not read domains — which is also why
`named_check` is **not** a blocker: the aggregate folds whatever legitimate active results exist
today, and named checks join when their own `CurrentCriterionAssessment` semantics do.

**Lifecycle and claims stay out.** The aggregate never reinterprets task completion, failure or
dispatch refusal — a completed turn may be `not_met` or `incomplete`, and a lifecycle failure may have
no assessable acceptance at all. `claim_conflict` remains excluded entirely; an adapter disagreeing
with the machine is audit context, not acceptance authority.

## D-2026-08-17-16 — One fidelity gap remains: the binder collapses eighteen resolver reasons into one (EFE DECISION, ACTIVE)

**Decision.** Recorded as a known, bounded gap rather than fixed in a documentation PR, and it is the
one thing standing between the current model and full compliance with PR9's stated requirement.

**The finding.** PR18's binder maps *any* unresolvable lineage to a single set reason,
`lineage_unavailable`. The resolver distinguishes **eighteen** causes — among them
`continuity_not_declared` (*nobody stated a relationship*), `continuity_legacy_unknown` (*this turn
predates continuity*), `malformed_lineage`, `cycle_detected` and `lineage_depth_exceeded` — and none
of that survives into the envelope.

**Why it matters.**
[D-2026-08-16-3](#d-2026-08-16-3--per-turn-assessment-has-two-dimensions-and-known-failure-dominates-uncertainty-efe-decision-active)
required that "we never asked" and "we cannot know what we asked" stay distinct, on the grounds that
collapsing them turns two different facts into one sentence. Under the current envelope the aggregate
*cannot* honour that, because the information is gone before it arrives. It also merges the
population-unknown family with genuine structural corruption, which
[D-2026-08-17-13](#d-2026-08-17-13--set-unavailable-and-criterion-unverified-are-different-facts-and-the-aggregate-must-keep-them-apart-efe-decision-active)
otherwise keeps apart.

**It is a fidelity gap, not a safety gap.** Every affected case is already `not_assessable` and fails
closed; no acceptance outcome is manufactured and nothing is reported as `incomplete` or `met` that
should not be. The loss is entirely in *how precisely Cofferdam can explain itself*, which is why the
runtime aggregate is judged ready to build and this is judged a defect to fix alongside it rather than
a reason to stop.

**The fix belongs in the binder, and it has a version consequence.** Carrying the resolver's own
reason onto the envelope — as a derived field, no persistence — is a change to what a
`CurrentAssessment` says, so it moves `CURRENT_ASSESSMENT_VERSION` 2 → 3 and changes every envelope
fingerprint. That is a real cost and the reason it is not smuggled into a docs PR. It should be
decided explicitly and, if taken, taken **before** `AGGREGATOR_VERSION = 1` is minted, so the
aggregate is built once against a stable envelope instead of twice.

## OPEN QUESTIONS

- **OQ-2 — no lockfile.** Dependencies declare lower bounds only. Fine for now; revisit when
  reproducible Ubuntu installs matter.
- **OQ-4 — YouTube search quota.** The documented default allocation is 100 `search.list` calls per
  day. Enough for personal use, and surfaced as its own error state; revisit only if it is actually
  hit in practice.
- **OQ-3 — TV+ search.** Deferred, not abandoned. A region-qualified address
  (`/{storefront}/search?term=`) does work, but Cofferdam cannot determine the account's storefront
  without probing Apple. Revisit if the browser companion — which can read the region from the tab
  that is already open and signed in — lands.

## Fable recommendations (advisory — not Efe decisions)

- **R-1** (*done 2026-08-01, commit `419f90f`*): the 18 files of Trust Core executor work that
  existed only as uncommitted worktree state are now preserved as a WIP commit on
  `pr3c2-candidate-b-execution`. Not merged, not reviewed, not continued.
- **R-2**: Backend in Python (FastAPI); UI served by the runtime itself; WebSocket events;
  port-based A/B switching; Guardian as discovery endpoint rather than full proxy in MVP;
  X11/Xorg session first; Playwright with a persistent Chrome profile. Detailed rationale and the
  open questions are in [`ROADMAP.md`](ROADMAP.md).
- **R-3** (*settled 2026-08-08 by D-2026-08-08-5*): do not integrate OpenClaw in Milestone 1–2;
  run a timeboxed spike before the Claude task milestone to decide whether its session/streaming
  layer earns its integration cost. The first half held — nothing was integrated. The spike is now
  moot: the Claude task milestones shipped natively (M2F, M2G), so there is no integration cost
  left for it to weigh.
