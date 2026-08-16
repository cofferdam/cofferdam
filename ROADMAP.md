# Roadmap — personal AI workstation

Seven milestones, ordered to put a visible product on a phone screen as early as possible, then
remote Claude, then the A/B self-update demonstration, then natural-language routing. Review
depth follows the post-pivot policy in [`DECISIONS.md`](DECISIONS.md) D-2026-08-01-6. Items
marked **OPEN QUESTION** are unresolved; each names the experiment that settles it.

**Read [Active implementation order](#active-implementation-order-recorded-2026-08-08-replanned-2026-08-11) first.**
The M1–M7 sections remain the reference for what each layer *is*, and the M2x sections are the
record of what shipped; **M2J is complete** and the work actually queued next is
**M2K → M2L → M2M**, and that section takes precedence wherever the two disagree.

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
- **A local model may classify intent and, from M2L, plan** — it may never execute arbitrary shell
  commands, and its output is always schema-validated before anything runs. The Local Planner is
  **advisory**: it drafts proposals, and every consequence passes through an existing validated,
  user-confirmed path (D-2026-08-11-2). Implementation stays delegated to cloud workers.
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

## Active implementation order (recorded 2026-08-08, replanned 2026-08-11)

Where the milestone sections below are the design reference, this is the queue. M2F (Task Core,
PR #20) and M2G (the Claude Code CLI adapter, PR #21) are merged on `main`; the delegated-task
lane exists and has been driven from a phone. Client architecture and authority for everything
below are fixed by [`DECISIONS.md`](DECISIONS.md) D-2026-08-08-1 … -6.

**M2H, M2I, M2I.5 and M2J are complete.** The queue from here is:

```
M2J  Workspace, Working Context, mind foundation, Context Builder   COMPLETE (merged + deployed)
M2K  Evidence & evaluation foundation, + machine reason codes       (deterministic, model-free)
M2L  Local Planner MVP                                             (advisory, confirm-by-default)
M2M  Remote operations completion — overview, dashboard, diagnosis
──── later, in this order unless evidence reorders it ────
M2N  Mind retrieval — required (backlinks first, vectors second)
M2O  Browser and desktop skills, productized from Track B
M2P  Codex adapter (second delegated worker)
M2Q  Fast/deep planner routing — only if Track D data justifies it
──── unchanged, still later ────
Guardian / A-B slots (M5) · self-update (M6) · Opera Companion · Trust Core re-entry
```

Two **parallel tracks** run outside the milestone gates, isolated from production — see
[Parallel tracks](#parallel-tracks-b-and-d-recorded-2026-08-11) below.

Recorded as [`DECISIONS.md`](DECISIONS.md) D-2026-08-11-1 … -12 and D-2026-08-12-1, which are the authority for the
sections that follow. The planning package that produced them is preserved as history in
`handoffs/replan-2026-08-11/`; it is a draft that was edited before adoption, not a second
roadmap.

**Before M2J PR1 merges:** the supervised pass over the inherited live-validation debt listed in
[`STATUS.md`](STATUS.md) ran on 2026-08-11 (D-2026-08-11-12). The blocking set — lifecycle,
authority, capability truthfulness and deployment integrity — is **cleared**. The remaining
media-feature walkthroughs are deferred, non-blocking debt and **do not block** the merge
(D-2026-08-12-1); they keep explicit states in STATUS rather than being absorbed.

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

### M2J — Workspace, Working Context, mind foundation, Context Builder

**Reshaped 2026-08-11 (D-2026-08-11-1). The recorded scope is preserved** — workspaces, project
templates, the code-owned model allowlist, Auto/Safe/Review profiles, `get_project_context`,
handoff and history surfaces — and gains the durable "what are we working on" state everything
downstream reads from.

- **Objective:** Cofferdam owns which workspace is active, what the current objective is, which
  task and worker are in flight, where the plan stands, what decision is pending, which evidence
  was last produced, and what step is expected next. Plus bounded, provenance-tagged context
  assembly over that state and over canonical Markdown memory.
- **Why now:** everything after it reads from it. `get_project_context` was deferred out of the
  Actions bridge because it "cannot be built honestly before the workspace model it reads from"
  (D-2026-08-09-1) — the same sentence applies verbatim to a local planner.
- **Sub-phases:**
  - **PR1 — workspace model + Working Context.** *Merged as `ae5c025` (#36) and deployed; see
    [`STATUS.md`](STATUS.md) and [`docs/WORKSPACES.md`](docs/WORKSPACES.md).* Workspaces over
    projects (host-owned config, the same validation posture as `task-projects.json`); the
    objective and its history; Working Context as durable state in SQLite under `state/`, **not** a
    second Markdown authority. Context is keyed **per workspace** rather than stored once, so
    switching cannot leak one workspace's objective into another. Task state and the delegated
    worker are derived on every read and never stored. No document-role or profile fields yet —
    they arrive with the components that read them.
  - **PR2 — mind access + grant + memory-proposal queue.** *Merged as `1c45b26` (#38) and
    deployed; see [`STATUS.md`](STATUS.md) and [`docs/MIND.md`](docs/MIND.md).* Project mind read
    from the project's own repository by **role** rather than filename, mapped by `documents` on the
    workspace — the field PR1 left out until something read it; the global vault behind an explicit
    host-owned grant in `config/mind-grant.json`, absent by default; the proposal → accept →
    hash-bound apply path (D-2026-08-11-4), on the device-token surface alone, with deletion absent
    rather than refused and no egress of any kind.
  - **PR2.1 — the `cross_project` global role + stable project-root authority.** *Merged as
    `f279fc2` (#40) and deployed; see [`STATUS.md`](STATUS.md) and [`docs/MIND.md`](docs/MIND.md).*
    A fourth Global Mind role, and the `cofferdam` project root moved off `$COFFERDAM_HOME` to the
    stable source checkout so project memory does not move with an A/B deployment
    (D-2026-08-13-1).
  - **PR3 — Context Builder.** *Merged as `31ab114` (#41) and deployed; see
    [`STATUS.md`](STATUS.md) and
    [`docs/CONTEXT.md`](docs/CONTEXT.md).* `LocalContextPack` assembly, bounded by an explicit
    budget in **UTF-8 bytes** (model-independent by construction), every part carrying
    `{source_kind, source_ref, observed_at}` — `user_instruction`, `working_state`, `plan`,
    `decision`, `memory`, `worker_result`, `machine_observed`, `external_model_output`,
    `planner_inference`, of which the first five are producible and the rest are declared and
    unreachable. Priority order: the user's current message (never truncated) → Working
    Context → workspace summary → the relevant plan section → recent decisions → latest evaluation
    summary → bounded global style/preference extracts. The evaluation slot is **empty and says
    so**: M2K does not exist, and no evaluator was written to fill a priority position. Selection
    is a reference a person recorded or a bounded structural slice, and each part is labelled with
    which. **No vectors in M2J** — semantic memory is M2N, and its candidates already have a typed
    seam into the builder.
  - **PR3.5 — `CloudContextProjection`, the egress boundary.** *Merged as `c24be24` (#42) and
    deployed; see [`STATUS.md`](STATUS.md) and
    [`docs/CLOUD_CONTEXT_PROJECTION.md`](docs/CLOUD_CONTEXT_PROJECTION.md).* The **second** of
    D-2026-08-11-5's two security objects, and a **hard gate on PR4** (D-2026-08-13-3). One narrow,
    code-owned, versioned profile — `project_context_external_v1` — that is deny-by-default and
    decides eligibility on the **decomposed semantic reference** rather than on `source_kind`,
    because `global:preferences` and `project:cofferdam:status` are both `memory`. Allowed:
    bounded project `status`, `plan` and `decisions`, plus four allowlisted Working Context fields
    projected from PR3's structured `fields` rather than from its rendered text. Denied by
    default: **all four Global Mind roles, including the `communication_style` and `preferences`
    that are in every local pack on this host**; the current user message; `design`; every other
    project. Content is sanitized as well as metadata — PR3 proved canonical Markdown legitimately
    contains slot paths and vault roots — with recognised paths replaced and declared, and
    credential-shaped material omitting the whole part rather than being rewritten. Its **own**
    16 KiB budget, not the pack's 64 KiB. **No transport, no surface, no model, no retrieval, no
    persistence**: projection prepares an object and never sends it.
  - **PR3.5.1 — sanitizer hardening.** *Follow-up to PR3.5; merged as `5afaa8e` (#43) and
    deployed.* Two recognition-layer defects found
    by PR3.5's post-deployment validation, neither ever externally reachable because nothing in
    that build imports the projection package: bare `TOKEN=` / `API_KEY=` / `SECRET=` assignments
    were not detected where prefixed ones were, and a doubled slash bypassed every path rule
    including the known host literals. Sanitizer and documentation only — no schema, policy id,
    allowlist, budget or surface change.
  - **PR4 — the read surface.** *Merged as `44e4994` (#44) and deployed; see
    [`STATUS.md`](STATUS.md).* The PWA workspace/context panel and a **read-only**
    `get_project_context` for the Custom GPT. The first OpenAPI edit since Gate B; note the `$ref`
    import pitfall PR2 found. **Gated on PR3.5 (D-2026-08-13-3):** no surface here may return a
    `LocalContextPack`, or anything derived from one, except a `CloudContextProjection` built by
    the egress policy. A surface additionally owns what projection deliberately does not —
    authentication, authorization, the destination contract, and the user-consequence semantics
    that go with them. **`syncWorkspace` is not here** (D-2026-08-13-4): it mutates, the egress
    policy authorizes no mutation, and M2M owns it. PR4 contains no workspace, project or memory
    mutation of any kind.
- **M2J is COMPLETE.** All seven sub-phases are merged and deployed; production is normalized on
  PR4 (`44e4994`) with the previous release retained as the rollback. As shipped, the read surface
  is `GET /api/projects/{project_id}/context` privately and
  `GET /v1/projects/{project_id}/context` (`getProjectContext`) on the Actions bridge; authority is
  **active-workspace-only**; the wire type is `CloudContextProjection` and nothing else, so a
  `LocalContextPack` never crosses transport; no Global Mind role and no current user message are
  eligible under `project_context_external_v1`; and there is no mutation anywhere in it —
  `syncWorkspace` remains M2M's. Details in [`STATUS.md`](STATUS.md).
- **The two context objects are separate types** (D-2026-08-11-5), and they are **separate
  milestones** (D-2026-08-13-3): PR3 owns the local pack, PR3.5 owns the projection. The local
  pack may be rich; anything leaving the host is a `CloudContextProjection` built by an explicit
  egress policy — global personal memory, unrelated-project memory, vault paths and project roots
  excluded by default, credentials structurally absent.
- **Role mapping, not filename dogma.** For Cofferdam itself, `STATUS.md`, `ROADMAP.md`,
  `DECISIONS.md` and `DESIGN.md` play the roles; **no `PLAN.md` is added** to satisfy a convention
  (D-2026-08-11-3). Long files are handled by section selection, which the builder needs anyway.
- **Security:** the vault grant is the product's second filesystem grant, so it gets the same
  treatment as project roots — absolute literal path, `lstat`/symlink checks, no traversal,
  re-verified at use. Memory apply is a device-token surface only. Bridge Actions stay names and
  ids: no roots, no vault paths, no prompts. One focused review on the grant and the proposal-apply
  path.
- **Persistence:** a new SQLite store under `state/`, separate from `tasks.sqlite3`, same
  WAL/FULL/0600 posture; `config/workspaces.json` with committed examples. Additive — no migration.
  Existing projects get *suggested* workspace entries the user confirms, never auto-created.
- **Validation:** the house style — switch workspace, set an objective, see it survive a daemon
  restart; a memory proposal accepted, one refused, one refused on drift; a pack built under budget
  with correct provenance; the Custom GPT retrieving project context from the iPhone.
- **Rollback:** absent config means current behaviour; the new database is deletable without
  touching tasks.
- **Not in scope:** no planner, no vectors, no automatic memory writes, no worker changes, no new
  public surface.
- **Review depth:** normal backend, plus the one focused review above. The profile semantics still
  deserve their own look, because a profile that quietly widens what a task may do is the failure
  worth designing against — and under this replan profiles govern **evaluation depth and
  confirmation defaults**, never what a task may do.

### M2K — Evidence and evaluation foundation

- **Sub-phases:**
  - **PR1 — adapter-reported change claims + the task-owned artifact foundation.** *Merged as
    `de0e7de` (#46) and deployed; see [`STATUS.md`](STATUS.md).* The **claim** side of evidence,
    which the product did
    not have: `ChangeClaim` (always `adapter_reported`, never verified), `ArtifactRecord` (always
    `os_observed`) and a bounded `ClaimIngestion` summary that makes refused or truncated
    submissions durable **without storing any rejected payload**, so a later bundle can tell a
    complete claim set from an incomplete one — three tables in an additive **schema v4** — with
    project-root containment and a
    code-owned secret-path deny list applied at **record time** per D-2026-08-09-3, a server-minted
    `artifact_id`, a machine-observed domain-tagged SHA-256 with no partial digest, and a bounded
    preview over a code-owned type allowlist. **No comparison, no verdict, no risk level, no check
    execution and no bridge Action** — `artifacts_supported` stays `false`. Adapters gain
    `AdapterOutcome.change_claims` with no command field, no root and no id authority
    (D-2026-08-11-7); only the code-owned validation adapter reports one, because neither Claude
    adapter has a structured claim source and prose is never parsed into claims.
  - **PR2 — the derived `EvidenceBundle` + exact turn/event provenance bounds.** *Merged as
    `52811dc` (#47) and deployed, with the live database migrated to schema v5; see
    [`STATUS.md`](STATUS.md).* The **comparison** side, and it is
    model-free. `EvidenceBundle` is **derived on read, never persisted** — no table, no serialized
    column — from claims, ingestion summaries, append-only event evidence and the new bounds, with
    **no Git execution, no filesystem read and no provider call**, so a bundle describes what was
    recorded rather than what the repository looks like now. An additive **schema v5** adds
    `task_turn_bounds` alone, because the PR1 audit proved exact turn attribution cannot be
    reconstructed from v4: **timestamps are not authority, event-sequence bounds are.** Bounds are
    written inside `_open_turn_locked` / `_close_turn_locked` in the same transaction as the turn
    lifecycle operation; pre-v5 turns receive **no inferred bounds** and report `legacy_unknown`.
    The relationship vocabulary is `path_agreed` / `claim_only` / `observed_only` and never a bare
    `agreed`: today's `git status` evidence proves a **path changed** and nothing about the
    operation, so `operation_agreement` is `unknown` and **zero `claim_conflict` relationships are
    emitted** — absence is not conflict. `assembler_version` and a domain-tagged
    `input_fingerprint` identify the inputs; **project-relative semantic paths are fingerprint
    inputs, absolute host paths are not**. One private turn-qualified route on the **device token
    only** — the Actions bridge is refused, `artifacts_supported` stays `false`, and the bridge
    gains no operation. **No evaluator, no verdict, no risk level, no check runner, no model.**
  - **PR3 — richer machine-owned Git observations + assembler v2.** *Merged as #48 (`d98c10f`) and
    deployed; see [`STATUS.md`](STATUS.md).* The **observation** side, which was the real
    ceiling on PR2: Cofferdam already asked Git for the operation and threw it away. The probe
    becomes `git status --porcelain=v1 -z --untracked-files=all` — Git's documented machine
    format, NUL-framed, raw paths, and **file-level** rather than collapsing a new directory into
    one record — and a flat `changed_paths` becomes structured `GitChange` records carrying a closed
    machine kind (`created`/`modified`/`deleted`/`renamed`/`unknown`), the raw `XY`, and **both**
    sides of a rename. Because `XY` is two columns, a composite status proves **two** facts
    (`RM` = renamed *and* modified), so agreement is decided against the whole fact set — a claim
    matching any proven fact agrees, and only a claim incompatible with *every* fact is
    contradicted. That is what stops a collapsed label manufacturing a false conflict. `EvidenceReference` gains two optional fields and **needs no schema bump**:
    old rows read back with `None`, which means *the operation was never established*. Assembler
    **v2** answers `operation_agreement` as `true`/`false`/`unknown` from one closed table, which
    makes the **first deterministic `claim_conflict`** possible — two positive machine facts that
    cannot both describe one path. Absence, legacy evidence, unmerged states and truncated
    observation sets are all still **not** conflict, and a conflict is **not a verdict**. Machine
    observation truncation becomes a durable, published fact. **The committed-work limit is
    recorded rather than papered over:** `git status` compares against the *current* HEAD, Cofferdam
    stores no pre-work revision, so a worker that commits leaves a clean tree and is not observed —
    PR3 deliberately does **not** add a revision diff it has no honest boundary for. Still **no
    evaluator, no verdict, no risk level, no check runner, no model.**
  - **PR4 — the durable per-turn pre-work Git baseline.** *Merged as `cf29b89` (#49) and deployed;
    see [`STATUS.md`](STATUS.md).* The boundary PR3 recorded the absence of. Before a
    worker turn is allowed to begin, the **host** reads the project's Git revision and working-tree
    state and commits it — machine-observed, never adapter-reported, prompt-supplied or
    caller-selected. Schema **v6** adds one additive table, `task_turn_git_baselines`, keyed
    `(task_id, turn_number)`; historical turns are **not backfilled** and a missing row means *no
    boundary was recorded*, never *the tree was clean*. The ordering is the point and it is
    structural rather than temporal: capture commits before `adapter.start` and before
    `adapter.send_followup`, on every path, and a test adapter asserts the durable row exists at its
    own first instruction. Because the adapter is invoked before the turn row is written — so a
    refusal leaves no turn behind — the foreign key names `tasks`, and "captured, then refused, so
    the turn never opened" stays representable. `present`/`unborn`/`unavailable`/`not_a_repository`
    are distinguished, no revision is ever invented, the object format is read rather than assumed
    (SHA-256 repositories produce 64-hex ids), a HEAD that moves across the observation is retried a
    bounded three times and then recorded as unstable, and a pre-existing dirty tree is a durable
    fact so PR5 can say changes did not necessarily start clean. **PR4 consumes none of it:** no
    `git diff baseline..HEAD`, `assembler_version` stays 2, no route, no bridge Action. Still **no
    evaluator, no verdict, no risk level, no check runner, no model.**
  - **PR5 — committed-work observations from the stored boundary.** *Merged as `e9f5e26` (#50) and
    deployed, with the live database unchanged at schema v6; see [`STATUS.md`](STATUS.md).* What
    PR4's boundary makes answerable: what the
    repository gained between the recorded pre-work revision and a stable HEAD observed after the
    adapter returned — the work PR3 structurally cannot see, because a worker that commits leaves a
    clean tree. **The schema stays v6** and there is no migration: the observation is immutable
    `task_events.evidence_json` on a dedicated `committed_range_observed` event, which gets its own
    evidence budget instead of competing with PR3's. Captured at the one host-owned point where the
    turn is guaranteed open — after the adapter returns and the turn row exists, before `_apply`,
    under the service lock — so the event's sequence falls inside the turn's own v5 bounds as
    arithmetic rather than as a later attribution. Only for `dispatch_state == turn_opened`: a
    refused dispatch and a dispatch that produced no turn stay the explicitly uncertain attempts PR4
    recorded. **A range is not a history:** `git merge-base --is-ancestor` establishes the relation
    first, its exit 0/1/128 kept apart, and a divergence is recorded rather than diffed — a tree
    diff across one reports another branch's files as deleted by a worker that deleted nothing.
    Rename detection and diff helpers are pinned on the argv rather than left to repository config,
    and `git diff --name-status -z` is parsed on its own grammar, whose rename records are
    source-then-destination — the opposite of porcelain's. Committed and uncommitted observations
    stay **separate domains** that may name the same path, and a boundary PR4 recorded as dirty,
    incomplete or unavailable may show change but may never produce a conflict. `assembler_version`
    becomes **3**, with no live Git at assembly. Still **no evaluator, no verdict, no risk level, no
    check runner, no model, no bridge Action.**
  - **PR6 — the immutable per-turn acceptance-criteria snapshot.** *Merged as `cd11232` (#51) and
    deployed, with the live database migrated to schema v7; see [`STATUS.md`](STATUS.md).* What a future evaluator evaluates **against**, and
    nothing more: after five PRs of evidence the database could describe what happened and held no
    criterion type, criterion set, criterion identity, fingerprint or per-turn criteria authority.
    Schema **v7** adds two additive tables — `task_turn_criteria`, one row per **reserved turn**,
    and `task_turn_criterion_items` — with historical turns **not backfilled**, no prompt parsed,
    and no claim converted into a requirement. Criteria are a **pre-work durable fact** on the PR4
    pattern: both the snapshot and the baseline commit before `dispatch_started`, which commits
    before the adapter call, and a test adapter finds the whole snapshot over a separate read-only
    connection at its own first instruction while `task_turns` is still empty — which is again why
    the foreign key names `tasks`. Three states, and the last two must never collapse: `present`, an
    explicit `not_provided` recorded before dispatch, and `legacy_unknown` for the absence of the
    row, which the schema refuses to let anyone write. The model is small and closed — `evidence`
    predicates (`path_changed`, `path_operation` over created/modified/deleted, `rename`) that the
    *already stored* rows can decide, plus `manual`, which means undecidable by machine and is
    neither passed nor failed. **No command criteria of any shape** — no shell string, argv, script,
    test command, executable path or `check_id` — because a criterion carrying a command is dormant
    execution authority waiting for a runner, and the check-command rule below is what it will use
    when it exists. Negative/set criteria ("nothing outside S") are **deferred**: they need a
    bounded structured path set and a stronger completeness semantics than PR2 or PR4 establish.
    Bounds **refuse rather than truncate**, because a bounded requirement set reads afterwards as
    the complete one. Snapshot and criterion identity are server-minted; the criteria fingerprint is
    a domain-tagged length-prefixed SHA-256 over stored facts only, stable across restarts and free
    of row ids, clocks, absolute paths and provider ids. Retry is PR4-conservative: a refusal does
    not re-open replacement, so a retry of a reserved turn uses the same snapshot, while a genuinely
    new follow-up turn may receive a new one. Internal `TaskService` input only — **no route, no
    request field, no bridge Action** — and `assembler_version` stays **3**. Still **no evaluator,
    no `EvaluationRecord`, no met/not_met, no verdict, no risk level, no confidence, no check
    runner, no model.**
  - **PR7 — deterministic criterion evaluation and the immutable `EvaluationRecord`.** *Merged as
    `7f21fc4` (#52) and deployed, with the live database migrated to schema v8; see
    [`STATUS.md`](STATUS.md).* The first PR that answers anything:
    for each supported criterion, whether the stored machine evidence for that exact turn satisfies
    it. Three values and no fourth — `met`, `not_met`, `unverified` — and **no task verdict, no
    aggregate, no pass/fail, no confidence, no risk, no model, no check runner and no command**, with
    no column any of them could occupy. Schema **v8** adds `task_turn_evaluations` and
    `task_turn_criterion_results`; the migration evaluates nothing and backfills nothing. The
    foreign key names `task_turns`, unlike PR4's and PR6's, because an evaluation may exist only for
    a turn that has already **closed** — which is also why it cannot be an event: PR5's observation
    was captured while the turn was open and took a sequence inside its bounds, whereas an event
    appended after the close would belong to no turn. Evaluation runs strictly after
    `closed_through_event_sequence` is durable, in a separate transaction, so a failure cannot touch
    a task's lifecycle; the resulting gap — closed turn, no judgement — is repaired by the *same*
    function at start-up, whose query excludes anything already evaluated, so repeated restarts
    produce one record. `EVALUATOR_VERSION = 1` is distinct from the schema, assembler and criteria
    model versions, and sits in the uniqueness constraint so a future version 2 never rewrites
    version 1. The record binds both identities in full — criteria `snapshot_id` **and** fingerprint,
    `assembler_version` **and** evidence `input_fingerprint` — and copies the bundle nowhere.
    **`path_changed` means a resulting observed repository effect, not any transient touch**;
    Cofferdam observes a boundary, not a process. **Machine evidence is the sole authority**: the
    evaluator does not read claims, ingestion or relationships at all, so a claim cannot satisfy a
    criterion, silence cannot fail one, incomplete claim ingestion downgrades nothing, and
    `claim_conflict` drives no result. Closure is **predicate-specific**: both observation domains
    must be read completely before absence is a finding; a pre-work boundary that is not
    `clean_complete` blocks **both** `met` and `not_met`, because PR4 records only a coarse tree-wide
    dirty word and a path that was dirty and then restored to HEAD leaves no observation at all; and
    domains are never collapsed — `created` in the range and `modified` in the tree are both true. A rename needs an explicit machine rename record and is **never** inferred
    from created-plus-deleted. `manual` is always `unverified`; `legacy_unknown` produces **no**
    record; `not_provided` produces a zero-result record the schema forbids from ever reading as a
    pass. Internal only — no route, no request field, no bridge Action — and `assembler_version`
    stays **3**.
  - **PR8 — the private read-only assessment surface and PWA panel.** *Merged as `059fdcb` (#53) and
    deployed — workstation and Actions bridge both run it from slot B, the live database is unchanged
    at schema v8, and the rollback is an exact slot flip to slot A at `7f21fc4` against that same
    database; see [`STATUS.md`](STATUS.md).* Everything M2K has stored since PR6 has been
    invisible; this publishes it and computes nothing. **No schema change (still v8), no evaluator
    change, no new stored fact.** One turn-qualified route —
    `GET /api/tasks/{task_id}/turns/{turn_number}/assessment` — returns criteria and evaluation
    together, because they are one audit question and two routes would let a client pair states that
    never coexisted. Route count 79 → 80, GET only, no rerun route, no mutation verb. Guarded by
    `require_token` rather than `require_task_caller`, so the **Actions bridge credential is refused**
    — the evidence route's precedent, for a stronger version of its reason. Read consistently under
    one hold of the store lock. The serializer is a **structural whitelist**: every key written out,
    no `asdict`/`vars`/`__dict__`, and wrong types refused rather than duck-typed. Three criteria
    states and four evaluation states are published as closed words — including `not_recorded` for a
    closed criteria-bearing turn with no record, which is an operational fact and **not** a pass — so
    a client never infers meaning from a null. **No aggregate** in the response, the serializer or the
    UI: no overall result, pass, fail, score, percentage, confidence or risk. The panel renders
    `Met` / `Not met` / `Could not verify`, with `unverified` in a **different badge class and a
    neutral tone** from `not_met`, because one is a finding about the work and the other is a
    statement about Cofferdam's reach. Evidence is **named** by `assembler_version` and
    `evidence_input_fingerprint`, never copied, and `claim_conflict` is absent entirely. No bridge
    Action, no public exposure. The `require_token` choice is an **intentional security boundary**,
    not an inconsistency to be tidied away — D-2026-08-16-1.
  - **PR9 — assessment aggregation and turn-continuity doctrine.** *Merged as `b2314f0` (#54);
    documentation only, so there was no deployment step.* **No schema, no route, no runtime aggregation, no code** — it
    settles the contract before the named check runner adds another mechanism that produces results.
    Three axes stay separate: **worker lifecycle**, **acceptance** and **verification reach**;
    `completed` never implies `met`, and `failed` never implies `not_met`. Per turn there are two
    dimensions rather than one enum: *availability* (`assessable` / `not_assessable`, with
    `not_provided` → `no_structured_criteria` and `legacy_unknown` → `historical_criteria_unknown`,
    neither ever a pass) and, only when criteria are `present`, an *acceptance outcome* of
    `met` / `not_met` / `incomplete`. The rule is ordered: a deterministic `not_met` dominates; any
    `unverified` yields `incomplete` and never `not_met`; only all-met yields `met`. A `manual`
    criterion is always `unverified` today, so any snapshot containing one is **capped at
    `incomplete`** — and manual completion is never inferred from prose, a tap, a claim or a model.
    `requires_human` stays orthogonal context rather than a competing outcome, so it cannot hide
    machine incompleteness. `claim_conflict` is excluded from aggregation entirely. **No task-level
    aggregate exists**: *accumulate-all* makes a task that created then deleted a file contradict
    itself, and *latest-turn-only* silently drops turn 1's feature and tests when turn 2 adds
    logging — so task acceptance stays **unavailable** until criterion continuity/supersession
    semantics exist, which is a prerequisite, must be explicit, must be frozen pre-dispatch, is
    authored by the planner or user and **never** by the worker or adapter, and needs an additive
    schema version. A future aggregate carries its own `AGGREGATOR_VERSION`, and is **derived on
    read** rather than persisted. Vocabulary avoids `success`/`failed`/`passed`, which already belong
    to lifecycle. D-2026-08-16-2 through D-2026-08-16-6.
  - **PR10 — the criterion continuity persistence foundation.** *Implemented on
    `m2k-pr10-criterion-continuity`, not merged and not deployed.* Persists the prerequisite PR9
    named, and **computes no aggregate**. **Schema v9**, additive:
    `task_turn_criteria_continuity` and `task_turn_criterion_supersessions`, created **empty** with
    **no backfill** — a turn that predates them has no row and reads `legacy_unknown`, forever.
    Three read states — `declared`, `not_declared`, `legacy_unknown` — where an undeclared dispatch
    writes an **explicit durable `not_declared`** rather than nothing, because "nobody said" and "we
    cannot know" must stay distinguishable. Four modes: `root` (no predecessor, checked against the
    database), `extend`, `replace`, `revise`. **`independent` is deliberately absent** — it answers
    neither "prior requirements remain" nor "they do not", so it would leave an aggregate guessing.
    Criterion-level supersession is a **bounded many-to-many** so a requirement may legitimately
    split or merge, capped at 64 relations and **refused over the cap rather than trimmed**. Lineage
    is **declared, never inferred**: matching description, fingerprint, path or ordinal is never
    authority, and the predecessor is bound by `predecessor_snapshot_id`, validated to exist, to
    belong to the same task and to come from an earlier turn. Frozen **pre-dispatch** with the
    criteria snapshot and the Git baseline, and immutable across retry, refusal and restart.
    Authority is the user or a future host-owned planner — **never the worker, never the adapter**:
    `AdapterOutcome` and `TaskContext` have no continuity field, and there is **no HTTP, bridge or
    PWA surface**, exactly as PR6 kept criteria internal. `CONTINUITY_MODEL_VERSION = 1` is bound
    into a deterministic `continuity_fingerprint`. **No `AGGREGATOR_VERSION`, no task verdict, no
    check runner, no command execution**; `EVALUATOR_VERSION` stays 1 and `ASSEMBLER_VERSION` stays
    3. Rollback is a **pair** — slot A at `7f21fc4` plus a verified pre-v9 backup — because the
    deployed PR8 runtime refuses a v9 database; that refusal was measured against the real deployed
    source and leaves the file byte-identical.
- **Objective:** an `EvidenceBundle` per turn, assembled from observations and structured claims;
  deterministic criteria checks; risk levels; and machine-observed failure reason codes attached to
  tasks. **Model-free.**
- **Why before the planner:** it is valuable on its own — the PWA and the Custom GPT can render
  expected-vs-observed with honest `unverified` rows before any local model exists — it is what the
  planner's evaluation feature reads, and it is what Track D's benchmark fixtures are made of. A
  planner built first would evaluate claims it cannot check (D-2026-08-11-1).
- **In scope:** the five-step artifact/change-claims Task Core PR that D-2026-08-09-3 already
  specifies; evidence assembly; the deterministic check runner (M6's concept pulled forward,
  narrowly); risk levels; reason-code records at the adapter and observer boundaries.
- **The authority rules are the point** (D-2026-08-11-6): every field carries its source kind;
  absent observation is `unknown`, never inferred; claims contradicting observations are kept and
  flagged rather than reconciled; deterministic checks run first; **the model layer may only
  downgrade, never upgrade**; worker-reported success never overrides missing evidence.
- **Check-command authority is fixed and narrow** (D-2026-08-11-7): checks are code-owned named
  checks or host/operator-owned validated definitions referenced by **stable id**. The planner, the
  worker, a remote caller and a task prompt **never supply executable text**. Literal `argv`, no
  shell, validated `cwd`, bounded timeout, bounded output, off by default per project. One focused
  review — this is the first surface on which Cofferdam runs a project-scoped command.
- **Reason codes begin here**, attached to task failures where the adapter boundary can classify a
  real error; the consolidated overview is M2M's (D-2026-08-11-8).
- **Persistence:** additive Task Core schema versions, one per sub-phase that needs durable shape,
  with the same additive-only discipline as v2 and v3. **Schema v4** is PR1's claim/artifact
  foundation (`task_change_claims`, `task_artifacts`, `task_claim_ingestion`). **Schema v5** is
  PR2's exact turn/event provenance bounds (`task_turn_bounds`) — needed because turn attribution
  provably cannot be reconstructed from v4 durable data, since timestamps are not an authoritative
  shared boundary between a turn and an event sequence. **Schema v6** is PR4's pre-work Git baseline
  (`task_turn_git_baselines`); PR5 needed none and left it at v6. **Schema v7** is PR6's
  acceptance-criteria persistence (`task_turn_criteria`, `task_turn_criterion_items`) and holds
  criteria only — there is no result, verdict or evaluation column anywhere in it. **Schema v8** is
  PR7's evaluation persistence (`task_turn_evaluations`, `task_turn_criterion_results`), which holds
  per-criterion results and, deliberately, no aggregate of them. The
  **`EvidenceBundle` itself is derived, not persisted**: it is assembled on read from stored
  immutable facts and has no table, so later criteria and evaluation records will refer to an
  evidence snapshot by `(task_id, turn_number, assembler_version, input_fingerprint)` rather than by
  copying it. PR6 is the other half of that pairing: a future deterministic `EvaluationRecord` binds
  `(task_id, turn_number, criteria snapshot identity/fingerprint, assembler_version,
  input_fingerprint)`, which is why **both** identities are frozen before the worker starts and
  neither is recomputed at evaluation time. Its three-valued result vocabulary is expected to be
  something equivalent to `met` / `not_met` / `unverified`, and the doctrine is that **evidence
  limitations map to `unverified`, never to `not_met`** — `legacy_unknown` criteria, incomplete
  observations, incomplete claims, a dirty committed-range boundary, diverged history and
  unavailable Git evidence all land there, and a `claim_conflict` is likewise **not** a
  task-failure verdict. Any later version is its own decision; "all M2K persistence is v4" was true
  only of PR1.
- **Validation:** a real task whose claims disagree with Cofferdam's git observations renders a
  flagged conflict; a task with criteria shows verified/unverified truthfully; a network cut
  mid-turn produces `NETWORK_UNREACHABLE`/`PROVIDER_UNREACHABLE` on the task.
- **Not in scope:** no model narrative, no automatic review of every task, no artifact *content*
  serving beyond the bounded preview the five-step PR defines.
- **Review depth:** normal backend, plus the focused check-runner review.

### M2L — Local Planner MVP

One model, one role, advisory throughout (D-2026-08-11-2).

- **Objective:** conversation, drafting, delegation through existing validated paths, an evaluation
  narrative over evidence bundles, and honest refusal when the context does not support an answer.
- **In scope:** a per-workspace planner conversation in the PWA, Turkish-first, over Context
  Builder packs, persisted bounded; a closed starter intent set — explain status, draft a worker
  task, draft a follow-up, recommend a next step, answer plan questions, and say "I don't know" or
  ask for clarification when the pack does not support an answer; worker-prompt drafting with
  acceptance criteria taken from the plan checkpoint; a `MEDIUM` evaluation narrative that
  distinguishes what the worker *claimed* from what Cofferdam *observed*.
- **Confirmation is explicit, by default, for every consequential proposal. There is no autonomous
  planner → worker continuation in this milestone.**
- **Placement:** `cofferdam/workstation/planner/` plus routes; the model runtime is a separate
  loopback process with its own systemd user unit, reached through a replaceable provider client
  (D-2026-08-11-10). The planner is not a `TaskAdapter` and does not speak to Task Core through the
  Actions bridge.
- **Model choice comes from Track D**, not from intuition. Qwen3.5-9B quantized is the current
  candidate and not an architectural dependency.
- **Security:** off by default behind its own flag; no bridge exposure; proposals-only writes;
  structured output schema-validated before use, never best-effort-parsed; bounded conversation
  store under the existing `no-store` content rules. One focused review on the
  proposal → confirm → create path, because it is a new route to task creation.
- **Explicitly out:** autonomous continuation of any kind · browser/actuator invocation · the
  ChatGPT browser skill · memory writes beyond proposals · multi-model routing · fast/deep second
  models · vector retrieval · Codex · voice · automatic memory writes · any exposure of the planner
  through the bridge or any new public surface.
- **Validation:** a live phone run in Turkish, end to end — converse, draft, confirm, worker runs,
  bundle assembles, planner explains with the claim/observation distinction intact, recommends a
  follow-up — plus the refusal case demonstrated, plus a truthful `planner_unavailable` when the
  model runtime is stopped, with tasks unaffected.
- **Review depth:** normal backend, plus the focused review above.

### M2M — Remote operations completion

- **Objective:** answer "what is Cofferdam doing right now, and what happened while I was away" in
  one glance.
- **In scope:** a consolidated `GET /api/status/overview` (host, services, planner, actuators,
  workers, workspace, attention) carrying names, ids and states only — no prompts, no paths, no
  secrets, every claim stamped with `observed_at` and its method; typed `/ws` events for health and
  working-context transitions; the workspace dashboard panel in the existing PWA; deterministic
  diagnosis synthesis over the M2K reason codes; the `syncWorkspace` Action — **M2M owns it
  outright** (D-2026-08-13-4), because it mutates and M2J PR3.5's egress policy authorizes no
  mutation; and retry UX wired to idempotent replays.
- **Diagnosis states its confidence** — `observed`, `likely`, `unknown` — and when evidence is
  insufficient the rendered sentence says Cofferdam could not determine the cause
  (D-2026-08-11-8). Consequential operations are never retried automatically.
- **The dashboard is the PWA**, over the tailnet. It is not published through the tunnel, and no
  second public origin is added (D-2026-08-11-11). It is now also a decision surface — memory
  proposals are accepted here — which is exactly why acceptance never reaches the bridge.
- **Validation:** the failure walkthrough executed for real — pull the cable mid-task, kill the
  helper, reboot the host, reconnect from the phone — each showing the specified truthful state,
  with the evidence table in [`STATUS.md`](STATUS.md).
- **Review depth:** normal backend.

### Parallel tracks B and D (recorded 2026-08-11)

Both are **isolated experiments outside the milestone gates**, run against no production
component and merged into nothing. They are recorded here so their results have somewhere to land,
not scheduled as milestones.

**Track B — browser actuator feasibility and provider comparison** (D-2026-08-11-9). A
provider-neutral `BrowserActuator` boundary, and one narrow user-triggered single-shot spike run
identically against three candidates — Playwright on a dedicated Chrome/Chromium profile, Kimi
WebBridge if its Ubuntu local-agent path can be driven semantically, and BrowserSkill. The spike:
a known logged-in ChatGPT conversation → a nonce-tagged exact prompt → submit → wait for truthful
completion → extract only the final assistant response → stop. Semantic automation only
(D-2026-08-04-7): a provider that works by screenshots and coordinates is out regardless of its
other merits. A dedicated automation profile, never the daily browser. Lives in
`experiments/browser-actuator/`, importing nothing from `cofferdam/`. Productized, if at all, in
M2O.

**Track D — Ollama operations and the Cofferdam planner benchmark** (D-2026-08-11-10). Host
provisioning as an ops task with no repository change, plus a harness that feeds fixture packs to
a `(endpoint, model)` pair and scores the work Cofferdam actually does: messy Turkish intent
understanding, project/context understanding, plan extraction, worker-prompt quality, follow-up
quality, result explanation, expected-vs-observed evaluation, unsupported-claim detection, tool
selection, deciding **not** to act, and asking for clarification. Deterministic scoring where
possible; rubric scoring labeled advisory. **Real and private examples stay local-only**; committed
fixtures are synthetic or public-safe until an explicit review decision says otherwise. Track D
must produce numbers before M2L's model choice is frozen.

### Later, in this order unless evidence reorders it

These are milestones, not ideas. They come after M2M and none of them blocks it.

**M2N — Mind retrieval. Required, not optional.** Canonical Markdown gains **two** complementary
derived relationship mechanisms: explicit links and backlinks, which are intentional and
human-readable, and **semantic/vector retrieval**, which surfaces memory that is relevant but was
never explicitly linked and does not share the words used to look for it. The point is the
behaviour: a new idea should reach the prior decisions and context it actually relates to, so the
planner can raise a contradiction or a decision it affects rather than waiting to be asked the
right way. Backlinks and the wikilink graph first, embeddings second — the explicit graph is
cheaper, exact and readable on its own. The index stays derived, rebuildable, discardable,
provenance-preserving, **local by default** and never canonical; where it and the Markdown
disagree, the Markdown is right. Retrieval reads only: any resulting change to memory still goes
through MemoryProposal → explicit private acceptance → hash-bound apply. See
[`DECISIONS.md`](DECISIONS.md) D-2026-08-08-6 and D-2026-08-12-4.

**M2O** browser and desktop skills productized from Track B · **M2P** Codex app-server as a second
delegated worker and reviewer in Lane B, which needs nothing new architecturally once M2K's claims
contract exists · **M2Q** fast/deep planner routing, only on Track D evidence.

### Optional or conditional, genuinely unordered

Not milestones, and none is committed to: an optional OpenClaw client under D-2026-08-08-5 · an MCP
or App transport, only when it materially improves the Actions path that has been proven to work ·
voice, STT and TTS.

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

> **Largely subsumed by the planner track (2026-08-11).** M2L handles natural language → typed
> action through the planner's proposal path — schema-validated, user-confirmed, and reading a
> provenance-tagged context pack rather than a bare classification prompt. M7 remains the reference
> for **deterministic level-1 and level-2 routing** if that is ever built separately as a
> model-light fast path; its "the model output is never executed directly" rule is unchanged and is
> the same rule D-2026-08-11-2 applies to planner proposals.

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
update path; voice; additional hosts; deeper multi-agent work. (**Obsidian moved forward**: an
Obsidian-*compatible* vault is M2J's global mind and retrieval over it is M2N. Integration with the
Obsidian application itself remains out — see D-2026-08-11-3.)

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
- **Ollama** — optional, and the initial recommended local-model runtime behind a replaceable
  provider boundary (D-2026-08-11-10): its own systemd user unit on loopback, with llama.cpp server
  as a drop-in alternative behind the same client. Without it, level-2 routing degrades to level-1
  buttons plus level-3 delegation, and the planner routes answer `planner_unavailable` while
  everything else is untouched. No exact model or tag is an architectural dependency.
- **Remote-desktop fallback** (e.g. Sunshine/Moonlight or RustDesk installed beside Cofferdam) —
  optional escape hatch for raw screen control; never integrated into Cofferdam's code.
