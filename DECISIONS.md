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

## OPEN QUESTIONS — require an explicit Efe decision

- **OQ-1 — the GitHub repository `cofferdam/cofferdam` is PUBLIC, not private.** Verified
  2026-08-01: `visibility: PUBLIC`, created 2026-07-09, Apache-2.0, 1 stargazer. Only the Trust
  Core through PR3c1 (`8fdb69f`) has been pushed; the pivot documentation, the M1 implementation,
  and the PR3c2 WIP executor are **local-only**. Pushing them publishes them immediately and
  irreversibly. Private planning material (`C:\cofferdam\handbook`) is outside the repo and
  unaffected. **Nothing has been pushed pending this decision.** Options: (a) push as-is,
  accepting publication now; (b) make the repository private, push, and re-publish deliberately
  later; (c) push only `main` + `feat/m1-remote-control-skeleton` and keep the unreviewed PR3c2
  WIP local. Until this is settled, the only off-machine backup is the git bundle.
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
