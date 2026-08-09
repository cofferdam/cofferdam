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
