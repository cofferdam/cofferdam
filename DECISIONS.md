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

## OPEN QUESTIONS

- **OQ-2 — no lockfile.** Dependencies declare lower bounds only. Fine for now; revisit when
  reproducible Ubuntu installs matter.

## Fable recommendations (advisory — not Efe decisions)

- **R-1** (*done 2026-08-01, commit `419f90f`*): the 18 files of Trust Core executor work that
  existed only as uncommitted worktree state are now preserved as a WIP commit on
  `pr3c2-candidate-b-execution`. Not merged, not reviewed, not continued.
- **R-2**: Backend in Python (FastAPI); UI served by the runtime itself; WebSocket events;
  port-based A/B switching; Guardian as discovery endpoint rather than full proxy in MVP;
  X11/Xorg session first; Playwright with a persistent Chrome profile. Detailed rationale and the
  open questions are in [`ROADMAP.md`](ROADMAP.md).
- **R-3**: Do not integrate OpenClaw in Milestone 1–2; run a timeboxed spike before the Claude
  task milestone to decide whether its session/streaming layer earns its integration cost.
