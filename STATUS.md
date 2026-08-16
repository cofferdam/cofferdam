# Status

Accurate as of **2026-08-13**. **M2J is complete**: all seven sub-phases are merged and deployed,
and production is normalized on PR4 (`44e4994`). See *M2J closeout* immediately below. The queue
from here is **M2K → M2L → M2M**.

**M2I.5 is complete**: PR #34 is merged as `2386a54`, production
normalization completed, and the live host runs that commit with the Claude Code adapter, the
Claude Agent SDK adapter, explicit host-owned delegated-adapter selection, Task Core as authority,
the private Custom GPT Actions bridge, same-session clarification continuation and follow-up,
structured single-choice `AskUserQuestion` with Cofferdam-minted option ids, a tailnet-private
PWA/API, and only the narrow Actions bridge public.

**The work after it was replanned on 2026-08-11**: M2J is preserved and reshaped, and the queue was
**M2J → M2K → M2L → M2M** with two isolated parallel tracks. M2J is now complete, so the
remaining queue is **M2K → M2L → M2M**. See [`ROADMAP.md`](ROADMAP.md) and
[`DECISIONS.md`](DECISIONS.md) D-2026-08-11-1 … -12 and D-2026-08-12-1; the planning package is
preserved as history in `handoffs/replan-2026-08-11/`.

## M2J closeout

**All seven sub-phases are merged and deployed.** Each has its own record below; this is the index.

| Sub-phase | What it added | Merge |
|---|---|---|
| PR1 | workspace model + durable Working Context | `ae5c025` (#36) |
| PR2 | mind access by role, host-owned grant, `MemoryProposal` | `1c45b26` (#38) |
| PR2.1 | the `cross_project` global role + stable project-root authority | `f279fc2` (#40) |
| PR3 | `LocalContextPack` — the deterministic Context Builder | `31ab114` (#41) |
| PR3.5 | `CloudContextProjection` — the egress boundary | `c24be24` (#42) |
| PR3.5.1 | projection sanitizer hardening | `5afaa8e` (#43) |
| PR4 | read-only project-context surfaces | `44e4994` (#44) |

**Production as normalized on PR4.** Both the workstation and the Actions bridge run `44e4994` on
slot B; slot A is retained unchanged at `5afaa8e` (PR3.5.1) as the rollback.

- **Private route:** `GET /api/projects/{project_id}/context`
- **Public bridge route:** `GET /v1/projects/{project_id}/context`, operationId `getProjectContext`
- **Authority is active-workspace-only.** A `project_id` resolves to the one enabled workspace
  naming it, and that workspace must be the active one. Every other resolution refuses under its
  own reason code — `project_not_found`, `project_disabled`, `workspace_not_configured`,
  `workspace_ambiguous`, `workspace_disabled`, `workspace_not_active` — and none carries a path.
  The caller supplies no root, path, policy, role or redaction input.
- **`CloudContextProjection` is the only wire type.** `serialize_project_context` refuses anything
  else by type, so a **`LocalContextPack` never crosses transport**.
- **No Global Mind egress** under `project_context_external_v1` — all four roles stay denied —
  and **no current user message**, which the pack is built without rather than with a fake one.
- **Read-only.** No mutation of any kind: no workspace switch, no objective edit, no proposal, no
  task. **`syncWorkspace` remains M2M's** (D-2026-08-13-4).

**M2K has begun.** PR1 — the adapter-reported change-claim and task-owned artifact foundation —
is **merged (#46, `de0e7de`)**. PR2 — the derived `EvidenceBundle` and exact turn/event provenance
bounds — is **merged (#47, `52811dc`) and deployed**, with the live task database migrated to
**schema v5**. PR3 — richer machine-owned Git observations and assembler v2 — is **merged (#48,
`d98c10f`) and deployed**: workstation and Actions bridge both run it from slot A, the live schema
is unchanged at v5 because PR3 needed none, and `assembler_version` is 2. PR4 — the durable
per-turn pre-work Git baseline — is **merged (#49, `cf29b89`) and deployed**: workstation and
Actions bridge both run it from slot B, and the live task database is migrated to **schema v6**,
with slot A retained at `d98c10f` plus a verified pre-migration schema-v5 backup as the rollback
pair. PR5 — committed-work Git observations from that boundary — is **merged (#50, `e9f5e26`) and
deployed**: workstation and Actions bridge both run it from slot A, the live schema is unchanged at
**v6** because PR5 needed none, and `assembler_version` is **3**. The immediate rollback is slot B
at `cf29b89` against the same live schema-v6 database; the pre-PR5 backup is deeper recovery only.
PR6 — the immutable per-turn acceptance-criteria snapshot — is **merged (#51, `cd11232`) and
deployed**: workstation and Actions bridge both run it from slot B, and the live task database is
migrated to **schema v7**. Because the PR5 runtime refuses a v7 database, the rollback is a **pair**
— slot A at `e9f5e26` together with the verified pre-v7 schema-v6 backup — rather than a slot flip.
PR7 — deterministic criterion evaluation and the immutable `EvaluationRecord` — is **merged (#52,
`7f21fc4`) and deployed**: workstation and Actions bridge both run it from slot A, and the live task
database is migrated to **schema v8**. Because the PR6 runtime refuses a v8 database, the rollback is
a **pair** — slot B at `cd11232` plus the verified pre-v8 schema-v7 backup — rather than a slot flip.
PR8 — the private read-only assessment surface and PWA panel — is **merged (#53, `059fdcb`) and
deployed**: workstation and Actions bridge both run it from slot B, and the live task database is
**unchanged at schema v8**. It changes no schema, no evaluator and no stored fact: it publishes what
PR6 and PR7 already froze. Because there is no schema change, no new stored fact and no DB-format
change, the immediate rollback is an **exact slot flip** back to slot A at `7f21fc4` against the
**same live schema-v8 database** — no backup restore is required, and that was proven by running the
slot A runtime against a consistent copy of the live v8 database before the flip was relied on.
The workstation declared route count is **80** (79 `APIRoute` plus the one WebSocket; the static PWA
mount is not counted), the one addition being
`GET /api/tasks/{task_id}/turns/{turn_number}/assessment`. That route is guarded by
**`require_token`, deliberately not `require_task_caller`**, so the **Actions bridge credential is
refused** on it — see D-2026-08-16-1. The bridge itself is unchanged at **10 routes / 9 authenticated
operations** with `artifacts_supported` false. Every one of these still holds: **no task verdict,
no pass/fail, no aggregate, no risk levels, no confidence, no model, no check runner, no planner**,
and none of PR3 through PR8 adds any of them.

PR9 — the assessment aggregation and turn-continuity doctrine — is **docs/design only**: it adds no
schema, no route, no runtime aggregation and no code at all. It settles what a future aggregate may
and may not say, so that the named check runner's results arrive to a consumer whose contract already
exists. See *Assessment aggregation doctrine* below and D-2026-08-16-2 through D-2026-08-16-6.

**M2H is complete and merged**, closing the M1 post-reboot gate;
M2F Agent Task Core and M2G the Claude Code adapter merged; the isolated Custom GPT Actions mobile
probe passed; client architecture and the active roadmap recorded as
[`DECISIONS.md`](DECISIONS.md) D-2026-08-08-1 … -6.

**M2I is complete and merged** (PRs #28–#31, the last squash-merged as `1a7d66b`). **M2I.5 PR1 is
merged** as `e078251` and **PR2 — Gate A — as `de15bd73`.** **M2I.5 PR3 — Gate B — is implemented,
deployed to the candidate slot and validated live on a branch.**

Gate A is done and the private Custom GPT is connected. A dedicated HTTPS origin reaches a
loopback-only Actions bridge through a Cloudflare Tunnel whose ingress names one hostname and one
service and answers 404 to everything else.

**Gate B is done: the Agent SDK runs in production and the structured clarification round trip is
demonstrated through the real private Custom GPT, from the native iPhone app.** One task in a new
disposable `agent-sdk-sandbox`: the agent asked one real `AskUserQuestion`, the GPT displayed the
two options, the person answered by position, and the GPT submitted the **Cofferdam-minted
`option_id`** — not the digit, not the label, with the free-text field `null`. The answer is
recorded with source `future_gpt_bridge`, the **same provider session** carried the continuation and
then one normal follow-up, and `finishTask` released the session. Two turns, one clarification, one
accepted answer, two results, and the disposable sandbox byte-identical afterwards. Sanitized
evidence: [`docs/checklists/m2i5-gate-b-validation.md`](docs/checklists/m2i5-gate-b-validation.md).

All four consequential Actions — `createTask`, `submitChoiceAnswer`, `sendFollowup`, `finishTask` —
were driven from the **native iPhone app**, which prompted rather than mutating silently. That
closes the gap Gate A left, where only the two read Actions had been exercised on mobile.

Both Claude adapters are now registered at once, which is why **the delegated adapter is an explicit
host decision** rather than the first entry of a list. That list is sorted at load, so "first" had
quietly meant *alphabetically first*; see the milestone record below.

**The main API and the PWA remain private**, reachable only on the existing Tailscale bind. They are
not in the tunnel's ingress, so Cloudflare cannot reach them — an absence, not a rule that denies.
The bridge is still loopback-only, the tunnel still names one hostname, and the external Action
surface is still the same nine operations against an **unchanged** OpenAPI document — enabling a
second adapter required no Custom GPT edit.

What Gate B does **not** claim: only the **single-choice** question shape is supported. Free text,
multiple choice, several questions at once and "Other + custom text" remain unsupported and return a
bounded unsupported result; none was widened to fit, and no live evidence about them was produced.
The project root also remains a configuration boundary the CLI is asked to respect, **not a kernel
sandbox**.

Update this file when a category changes, not on every commit.

## Merged (on `main`)

- **M2H — supervised Claude Remote Control** (Lane A), PRs #23, #24, #25, #26 and #27, the last
  squash-merged as `0818d25`. **Complete.** Per-project native Claude hosts as systemd user
  service instances, a fail-closed link-capture boundary, the confirmed native URL format, the
  Project Workstation card and secure open flow, and the cold-reboot validation that closes the
  M1 post-reboot gate. Lane A reads no transcript and injects no prompt, permanently. Detail in
  the milestone record below.

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

**M2I is closed.** PR4 merged as `1a7d66b` with its real-phone validation passed. **M2I.5 is closed
too** — PR1 `e078251`, PR2 `de15bd73`, PR3 `2386a54` (#34). The queued work is
M2J → M2K → M2L → M2M; see [`ROADMAP.md`](ROADMAP.md).

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

### CLOSED RELEASE GATE — M1 post-reboot auto-start is validated

**Closed 2026-08-09 by the M2H PR4 cold-reboot validation.** It stayed open through every
milestone since M1 because no earlier work changed boot behaviour; M2H was the first that did, so
its unattended-reboot validation and this gate were always the same step.

The reboot was real and the evidence is from the boot itself, not from a description of it. Boot
id went from `618fd2db-…` to `a2c7860c-…`.

| Observed | Timestamp |
|---|---|
| machine booted | 2026-08-08 18:10:34 |
| `tailscaled` started (system, enabled at boot) | 18:11:14 |
| user manager `user@1000.service` started, **class `manager`, no session** | 18:11:14 |
| `cofferdam-workstation.service` started | 18:11:14 |
| Tailscale address still absent — daemon logged its bounded wait | 18:11:14 |
| bound and serving on `100.116.199.35:7101` | 18:11:20 |
| **first phone connection over the tailnet** | 18:11:24 |
| graphical session first active | **2026-08-09 00:59:09** |

So the phone reached Cofferdam **50 seconds after power-on and 6 hours 48 minutes before anyone
logged into the desktop.** Every item this gate listed as unverified is now observed:

- the user service started automatically through lingering with nobody logged in;
- `tailscaled` was not ready in time, and that was fine — the daemon waited 6 seconds for its own
  address rather than dying in the restart loop this gate was written about. `NRestarts=0`,
  `Result=success`, no start-limit failure;
- the listener re-bound to the Tailscale address unattended, and to nothing else;
- phone-over-tailnet access worked after an unattended reboot;
- the linger-before-login path is now covered by observation as well as tests.

Authentication survived: the token file persisted at mode 0600 and the phone's previously saved
token was accepted with no re-entry. Unauthenticated requests still returned 401.

**The last expectation from the old text is now observed too (2026-08-11).** Graphical-session
detection reporting `open_application`/`open_url` as false before login and true afterwards was
covered by tests only until the logout/login cycle ran; across 475 samples the pair tracked the
real session exactly, with zero mismatches in either direction — see the
[M2B record](#m2b--runtime-inventory). Automatic login is still not enabled on this host.

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
  The lifecycle behaviour at GDM and across a real login therefore remained **unverified on this
  host** for the M2B runtime. M2B2 does not change graphical-session lifecycle behaviour, so it
  did not close this gap and did not require a logout of its own.

  **Gap closed 2026-08-11 — the cycle ran.** A real GNOME logout to GDM, ~5m45s at the greeter,
  then a normal login, recorded by a bounded read-only recorder running as a transient unit of the
  systemd **user** manager (`KillUserProcesses=false`, `Linger=yes`), which is why it survived the
  logout that killed the graphical session. **475 samples over 40 minutes.** This time the
  transition is real and visible in the data: gnome-shell `5760` → **absent for 69 consecutive
  samples** → `52993`, and the user's wayland session `3` → `9`.

  Across every one of the 475 samples: the unit stayed `active/running` with **`MainPID` 43344
  unchanged** and `NRestarts=0`, `Wants=`/`BindsTo=`/`PartOf=` stayed empty, `/healthz` returned
  200, and unauthenticated `/api/status` returned **401**. Authenticated capability reporting
  answered at the GDM greeter with `open_application`/`open_url` **false**, and returned to
  **true** after login — **zero mismatches** against gnome-shell presence in either direction, and
  no stale value on either side of the transition. While logged out, GDM's *own* greeter wayland
  session was present under a different uid and was correctly **not** claimed as the user's. Two
  samples at the login instant recorded a client-side timeout on `/api/status` and reported
  nothing rather than something stale; `/healthz` stayed 200 through both.

  Nothing was mutated by the validation: tasks/events/turns were `25/473/3` before and after, all
  25 tasks terminal, and no provider helper was spawned.

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

  **Provider credentials are configured and functional on this host (verified 2026-08-11).** Both
  providers return real catalogue results — five selectable results each, through opaque result
  handles, with no URI, URL or key leaking into any response. Spotify's user OAuth is connected
  with all required scopes. The unconfigured path was verified end to end earlier and its
  behaviour is unchanged; the live provider validation in
  [`docs/MEDIA_RESULTS.md`](docs/MEDIA_RESULTS.md) is no longer outstanding.

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

  **Cold-start recovery validated on the real host 2026-08-11; the rest is deferred.** The first
  of the three fixes above — Play now with Spotify fully closed — now has a real result. One
  operation (`spop-3a0cbdd65646`): Spotify was absent beforehand, the launch was triggered *inside*
  the operation, a real Connect device appeared, and the exact selected result was confirmed
  playing in 9.36 s with no second dispatch. The remaining transport, queue and volume steps are
  `DEFERRED_NON_BLOCKING`, and device transfer is `BLOCKED_BY_PREREQUISITE` — one device on this
  host. Neither is a pass.

  **The `90-spotify-playback-validation` drop-in is stale and must not be applied.** It points at
  an unmerged feature clone. Production runs the merged A/B slot deployment, and this build is part
  of it.

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

  **Live walkthrough deferred, non-blocking (D-2026-08-12-1).** The endpoint exists in the merged
  production build and reports its state truthfully — `disconnected`, empty queue, `idle` — with no
  player window open, which is the correct answer rather than a fabricated one. The numbered
  walkthrough itself has **not** been executed and is `DEFERRED_NON_BLOCKING`; that is not a pass.

  **The `95-youtube-player-validation` drop-in is stale and must not be applied.** It points at an
  unmerged feature clone. Production runs the merged A/B slot deployment, and this build is part of
  it.

  **Not in this milestone:** seek, automatic queue continuation when a video ends, queue
  persistence, and the Opera Companion for Netflix/Prime Video/TV+.

**M2B does not change the M1 reboot gate.** It alters no boot behaviour, no systemd unit, and no
bind logic. Neither does M2B3A, M2B3A.1, M2C, M2D, M2E, M2F, or M2G. **M2H does**, which is why
the gate closes there.

## In progress (on a branch, not merged)

### M2K PR12 — inherited-active supersession validation

On `m2k-pr12-inherited-supersession`, from the merged `3bb9a5b`. **Implemented on a branch, not
merged and not deployed.**

Corrects the one semantic mismatch PR11 discovered between PR10's write-time validation and PR11's
read-time resolution, and closes it in the direction that makes the write agree with the read.

**The mismatch.** PR10 required a supersession's old-side criterion to be **stored in** the declared
predecessor's own snapshot. PR11's resolver requires it to be **active in** the predecessor's
resolved active set. The two disagree the moment a requirement is inherited: a criterion introduced
at turn 1 and still live at turn 2 through an `extend` is part of what turn 3 stands on, but it is
not one of turn 2's rows. PR10 therefore refused a legitimate revision, and the only workaround —
declaring turn 1 as the predecessor instead — silently cut turn 2's own criteria out of the lineage.

**The rule now.** For `revise`, the allowed old-side criterion ids are exactly *the criterion ids in
the resolved active set of the declared predecessor*. Not the ids the predecessor snapshot physically
owns. The stated reason for the old check was that a declaration must not retire a requirement from a
turn it never claimed to stand on, and the active set is a more faithful reading of that sentence.

**What stays refused, and this is most of the work.** A criterion an earlier `revise` already
retired; one a `replace` cut away; one belonging to another task; a nonexistent id; a criterion of
the *current* snapshot used as an old side. Each has its own closed reason, and each refusal is
atomic — a declaration with one good relation and one bad one writes nothing at all.

**A `revise` whose predecessor lineage cannot be resolved is now refused before dispatch.** If the
predecessor's own continuity is `not_declared` or `legacy_unknown`, or the lineage behind it is
malformed, there is no set for the revision to be a revision *of*. New reason
`continuity_predecessor_lineage_unavailable`. It is **not** downgraded to `replace` — that would be
Cofferdam declaring something the caller did not. This is the one place PR12 *narrows* what PR10
accepted, and it is deliberate.

**`replace` is untouched.** It still validates its predecessor's identity and still does not require
its active set, so the PR11 recovery property holds exactly: `legacy_unknown` → `not_declared` → an
explicit `replace` still resolves. `extend` carries no relations and gains no new check; `root` is
unchanged.

**No schema change, and no version moved.** Schema stays **v9**. The supersession row already meant
*this new criterion retires that old one* and never carried a claim about which snapshot the old one
sat in — the foreign key deliberately names `task_turn_criterion_items` at large. `continuity_fingerprint`
is byte-identical for the same declaration, `CONTINUITY_MODEL_VERSION` stays 1 and `RESOLVER_VERSION`
stays 1: PR12 changes which declarations are *accepted*, not what a stored one *means*, and no
existing row is reinterpreted, rewritten or revalidated.

**One active-set algorithm.** Write-time validation calls PR11's pure resolver over the shared
lineage fetch, now extracted as `TaskStore._lineage_graph_locked`, rather than reimplementing the
root/extend/replace/revise fold. A second copy could disagree with the read path, and the
disagreement would only ever surface as a stored relation the reader refuses — the worst place to
find it. Asserted from the syntax tree.

**Validation and persistence are one transaction.** The walk runs on the write connection inside the
`BEGIN IMMEDIATE` that will persist the declaration, so there is no read-then-write window in which
the predecessor lineage could move. Asserted by observing `in_transaction` during the walk, which is
`True` under `_write()` and `False` under the plain read helper.

**PR11's read-time checks stay.** Write-time prevention is an *additional* guarantee, not a
replacement: a restored database, an imported fixture or a future bug can still present a stale
relation, and `supersession_target_not_active` must still catch it on read.

## M2K records — the evidence foundation (written while each was on its branch)

M2K is **in progress**: PR1 through PR8 are merged and deployed; PR9 is merged (`b2314f0`, #54) and
needed no deployment because it changed only documentation; PR10 is merged (`1efd49b`, #55) and
deployed to slot A on schema v9; PR11 is merged (`3bb9a5b`, #56) and **intentionally not deployed**;
PR12 is on a branch.
See *In progress* above for PR12.

### M2K PR11 — the pure continuity lineage resolver

**Merged as `3bb9a5b` (#56) and deliberately NOT deployed.** Production remains on the PR10 runtime —
workstation and Actions bridge both from **slot A at `1efd49b`**, live schema **v9**, rollback runtime
slot B at `059fdcb`. PR11 adds no schema, no write path and no external surface, so *merged* and
*deployed* were separated on purpose rather than by omission: there is nothing in it a running
service needs, and the next deployment can carry it together with PR12. Do not read the merge as a
deployment. The record below was written while PR11 was still on `m2k-pr11-lineage-resolver`, from
the merged `1efd49b`, and is kept as it was written.

PR6 froze **what each turn required**. PR10 froze **what each turn said about the turn before it**.
Neither answers the question anything downstream has to ask first: *given those immutable
declarations, which immutable criteria are in force at this turn?* PR11 answers exactly that and
nothing beyond it. It is **not** an aggregate, there is no `AGGREGATOR_VERSION`, no task verdict, and
no code here or downstream that could produce one.

**No schema change. Schema stays at v9.** The resolver is pure read logic over rows PR6 and PR10
already persist, so there is nothing new to store — asserted by comparing the database byte-for-byte
across repeated resolutions and by checking no table name contains `active`, `lineage` or `resolv`.

**Derived on read, never persisted.** The sources are immutable and the function is deterministic and
versioned, so an answer can always be recomputed. Persisting it would add a write path, a recovery
path and a second place for the truth to live that could disagree with the first.

**`RESOLVER_VERSION = 1`**, distinct from `SCHEMA_VERSION`, `CRITERIA_MODEL_VERSION`,
`CONTINUITY_MODEL_VERSION`, `ASSEMBLER_VERSION` and `EVALUATOR_VERSION`. All six move for reasons of
their own, and a future version 2 that resolved the same rows differently must be **visibly**
different rather than silently reinterpreting a stored identity.

**The four modes, exactly.** `root` → this snapshot's criteria, no traversal. `extend` → the
predecessor's resolved active set, then this snapshot's criteria, with **no** deduplication by text,
path or fingerprint. `replace` → this snapshot's criteria, and the predecessor's active set is *not
required*. `revise` → the predecessor's resolved active set minus every criterion an explicit
relation retires, in place, then this snapshot's criteria.

**`replace` is a lineage cut point, and that is load-bearing.** An unknown predecessor blocks
`extend` and `revise` — both are statements *about* the prior active set — but it does **not** block
`replace`, which says the prior set is gone whatever it was. That is what lets a task recover: a turn
that predates continuity (`legacy_unknown`), or one nobody declared anything for (`not_declared`), no
longer poisons every later turn forever. The predecessor's *identity* is still validated — it must
exist, belong to this task and come from an earlier turn — because cutting a dependency is not the
same as believing a malformed declaration.

**A stale supersession target fails closed.** A relation is valid only if its old-side criterion is
**actually active** in the resolved predecessor set. Historical membership is not active membership:
a criterion retired two turns ago cannot be retired again, and the stale edge is refused rather than
skipped, because skipping it would leave the resolver asserting a set no declaration produces.

**Deterministic ordering, and nothing is sorted.** Surviving inherited entries keep their relative
order, removals happen in place, and this turn's own criteria follow in stored `ordinal` order. Never
by criterion id, text, path or fingerprint — the order somebody submitted requirements in is a fact
about the requirements.

**Closed unavailable vocabulary**, code-owned and never exception prose: `continuity_legacy_unknown`,
`continuity_not_declared`, `predecessor_unavailable` (carrying the underlying cause and the turn it
broke at), `predecessor_missing`, `predecessor_foreign_task`, `predecessor_not_earlier`,
`criteria_snapshot_missing`, `continuity_snapshot_mismatch`, `root_has_predecessor`,
`root_not_first_snapshot`, `relations_mode_mismatch`, `supersession_target_not_active`,
`supersession_predecessor_unknown`, `supersession_current_unknown`, `duplicate_active_criterion`,
`malformed_lineage`, `lineage_depth_exceeded`, `cycle_detected`. An unavailable result carries **no**
partial active set, because a caller given one would use it.

**A resolved empty active set is an answer, not a success.** A `root` or `replace` whose snapshot is
`not_provided` resolves to zero active criteria, which means *the declared requirement set is empty*.
It does not mean the task passed, and there is deliberately no vocabulary in the module a reader
could mistake for one.

**Read-time validation, and no repairs.** PR10 validates at write time; PR11 still refuses a
snapshot mismatch, a cross-task or later-turn predecessor, an impossible `root`, a mode disagreeing
with its relations, a missing snapshot or criterion, a duplicate active id, a cycle and an
over-deep chain — each proven against a real database corrupted through raw SQL, including one
shape only reachable with `PRAGMA ignore_check_constraints`. A corrupted row is returned exactly as
stored; nothing is rewritten on read.

**Bounded and terminating.** `MAX_LINEAGE_DEPTH = 256` and a visited set, so a hand-edited row cannot
make a start-up read walk forever. Over the bound is *unavailable*, never a truncated set.

**One coherent read snapshot.** A resolution walks several turns' rows, so it is exactly the read
that could otherwise mix database states. `TaskStore.lineage_inputs` holds a single deferred read
transaction across the whole fetch, which in WAL mode pins the snapshot without blocking any writer.
A differential test forces a second connection to commit mid-walk and asserts the criterion it adds
is **not** observed — the same test fails on the previous `_read()` helper, which is what makes the
transaction load-bearing rather than decorative.

**Pure resolver.** `resolve()` takes a frozen input graph and returns a result. No SQLite, no
filesystem, no Git, no subprocess, no socket, no provider, no clock, no mutation — asserted from the
syntax tree, again at runtime with those callables poisoned, and again by deleting the project
repository and getting a byte-identical fingerprint.

**Resolved fingerprint.** Domain-separated SHA-256 over the resolver version, the exact target, the
active set as *ordered* `(source snapshot id, criterion id)` pairs, and every consumed lineage step's
turn, snapshot id, mode, criteria fingerprint and continuity fingerprint. A `replace`'s predecessor
is deliberately **not** bound: the resolution never traversed it, and a hash claiming otherwise would
assert a dependency the answer did not have.

**Internal only.** `TaskService.resolve_active_criteria(task_id, turn_number)` and nothing else. No
HTTP route, no Actions Bridge operation, no PWA control, and the PR8 assessment response is
**unchanged** — a lineage read surface is its own review.

**One PR10 boundary discovered and pinned rather than widened.** PR10 requires a supersession's
old-side criterion to belong to the **declared predecessor's own snapshot**, so a `revise` cannot
retire a criterion it merely *inherited* through an earlier `extend` unless it declares that earlier
snapshot as its predecessor — which then cuts the intervening turn's own criteria out of the lineage.
The resolver does not loosen the rule; both the refusal and the supported alternative are asserted.


### M2K PR10 — the criterion continuity persistence foundation

**Merged as `1efd49b` (#55) and deployed**: workstation and Actions bridge both run it from **slot
A**, and the live task database migrated to **schema v9** with both continuity tables created empty —
0 declarations and 0 supersessions on the 25 historical tasks, 473 events and 3 turns, exactly as a
no-backfill design requires. Because v9 is a schema bump, the rollback is a **pair** rather than a
slot flip: the PR8 runtime in **slot B at `059fdcb`** plus the verified pre-v9 schema-v8 backup taken
before the migration. The record below was written while PR10 was still on
`m2k-pr10-criterion-continuity`, from the merged `b2314f0`, and is kept as it was written.

PR9 named the one fact standing between Cofferdam and a task-level answer: every turn's requirements
were stored and **the relationship between them was not**, so neither *accumulate every turn* nor
*only the latest turn counts* could be correct. PR10 persists that relationship. It does **not**
compute an aggregate, and there is no code here or downstream that could.

**Schema v9, additive, created empty and never backfilled.**
`task_turn_criteria_continuity` holds one declaration per turn;
`task_turn_criterion_supersessions` holds its lineage edges. No v8 table, column or row is touched —
asserted by comparing a real v8 database before and after the upgrade, table by table and row by row.
A turn that ran before this table existed gets **no row**, forever, and reads `legacy_unknown`.

**Three read states, and the third is absence.** `declared`, `not_declared` and — for a missing row —
`legacy_unknown`. The middle one is the point: a turn dispatched with nobody declaring anything gets
an **explicit durable `not_declared`** rather than no row, because *nobody said* and *we cannot know
whether anybody said* are different facts and only the first is recoverable. It is emphatically not
`extend`, not `replace`, not `independent` and not preserve-previous.

**Four modes.** `root` (no predecessor — a structural fact, checked against the database rather than
believed), `extend` (prior requirements remain, new ones added), `replace` (prior set wholly
superseded, nothing deleted), and `revise` (prior requirements remain **except** the ones named by
explicit supersession relations). **`independent` is deliberately absent**: it answers neither
"prior requirements remain" nor "they do not", so an aggregate reading it would have to guess exactly
where PR9 refused to.

**Criterion-level supersession, bounded many-to-many.** PR9 concluded a snapshot-level relation alone
cannot express partial revision, and this is that. One old criterion may be superseded by several new
ones and several old ones by a single new one — a split and a merge need no special case, and
forbidding either would have invented a direction the domain does not have. Bounded at **64
relations**, refused over the bound rather than trimmed.

**Lineage is declared, never inferred.** Identical description, identical fingerprint, identical path
and identical ordinal are all things two unrelated criteria can share, so none of them is authority.
Both sides of a relation are durable criterion ids. The predecessor is named by
`predecessor_snapshot_id` rather than by a turn number worked out at read time, and it is validated
to exist, to belong to **this** task, and to come from an **earlier** turn.

**Frozen before the worker exists.** Reserved against the same turn number as the criteria snapshot
and the Git baseline, immediately after the snapshot it describes and before `dispatch_started`. The
adapter's first instruction asserts, on a separate read-only connection so uncommitted rows cannot
satisfy it, that all three pre-work facts are already committed and already frozen while `task_turns`
still has no row. A retry of the same reserved turn, an adapter refusal and a restart all leave the
original declaration and its fingerprint exactly as they were.

**Nobody but the caller may declare it.** `AdapterOutcome` and `TaskContext` have no continuity
field; no HTTP route, no bridge Action and no PWA control carries one. Like criteria since PR6, it is
an **internal `TaskService` argument only**, and every existing caller passes nothing and keeps
working unchanged.

**`CONTINUITY_MODEL_VERSION = 1`**, bound into a deterministic `continuity_fingerprint` alongside the
state, mode, both snapshot identities and the relations in canonical order. No clock, no rowid, no
minted id, no provider or host path reaches it. **There is no `AGGREGATOR_VERSION`** — nothing
aggregates, and the constant would imply something does.

**Rollback.** v9 is a schema bump, so this is a **pair** — slot A at `7f21fc4` plus a verified pre-v9
backup — rather than a slot flip. The deployed PR8 runtime was loaded from `git show 059fdcb` and
handed a real v9 database: it refuses with `StoreUnavailable`, names the newer version in its detail,
and leaves the main file **byte-identical**, the schema version un-downgraded, both continuity tables
intact, and integrity and foreign keys clean.


### M2K PR8 — the private read-only assessment surface and PWA panel

**Merged as `059fdcb` (#53) and deployed**: workstation and Actions bridge both run it from slot B,
and the live task database is **unchanged at schema v8**. Because PR8 changes no schema, adds no
stored fact and alters no on-disk format, the rollback is an **exact slot flip** to slot A at
`7f21fc4` against the same live schema-v8 database — proven before the flip was relied on by running
the slot A runtime against a consistent copy of the live v8 database, where it opened without
migrating, served its routes, answered 404 for `/assessment`, and mutated nothing. The record below
was written while it was still on `m2k-pr8-assessment-surface`, from the merged `7f21fc4`, and is
kept as it was written.

PR6 froze the criteria and PR7 froze the evaluation, and both have been durable and **completely
invisible** — no route, no panel, no way to see either without opening the database. PR8 publishes
them. It computes nothing: **no schema change (still v8), no evaluator change, no new stored fact.**

**One route, not two.** `GET /api/tasks/{task_id}/turns/{turn_number}/assessment` returns criteria
and evaluation together, because they are one turn-qualified audit question and a reader needs both
or neither. Two routes would let a client pair criteria read at one moment with an evaluation read at
another, and would leave two HTTP contracts free to drift while describing one thing. Route count
moves **79 → 80**, and the delta is exactly this one GET.

**`require_token`, not `require_task_caller`** — a deliberate departure from the obvious choice, and
the reason is the Actions bridge. `require_task_caller` is what makes the bridge's ten task routes
work: it *accepts* the bridge credential. An assessment is Cofferdam's judgement about somebody's
work measured against what they asked for, which is further from the bridge's business than evidence
is, and the evidence route already set this precedent. `require_token` has never heard of the bridge
credential, so a bridge request arrives as an ordinary unauthenticated one and gets 401 — a stronger
guarantee than a check that rejects it, which a refactor could lose. The test asserts both halves:
refused here, still accepted where it belongs.

**Consistent read.** `TaskStore.turn_assessment_inputs` reads turn state, criteria and evaluation
under **one hold of the store's lock**. Criteria and evaluation are immutable once frozen, so
separate reads are *almost* safe — but a turn that closes mid-request can gain an evaluation between
two calls, and a response pairing criteria from before that commit with an evaluation from after
would describe a state that never existed. Every writer takes the same re-entrant lock, so holding it
closes the window with no new locking machinery.

**The serializer is a whitelist, structurally.** Every published key is written out literally; there
is no `asdict`, no `vars`, no `__dict__` and no loop over `__dataclass_fields__` anywhere in the
module — asserted from the syntax tree, because those idioms publish whatever a dataclass gains next.
The view functions **refuse the wrong type** rather than duck-typing, so a dict cannot arrive dressed
as a `CriteriaSnapshot`.

**Three criteria states and four evaluation states, all said out loud.** `present`, `not_provided`
(recorded, zero items, and shaped so it cannot be read as a pass) and `legacy_unknown` (no fabricated
snapshot, no fake empty set). For the evaluation: `recorded`, `criteria_legacy_unknown`,
`turn_not_closed`, and `not_recorded` — the last meaning a closed criteria-bearing turn has no record,
which is worth noticing and is **not** a pass, **not** a skip and **not** an `unverified` criterion
result. The word *pending* is deliberately absent: it invites polling and implies a record is owed.

**No aggregate anywhere.** No overall result, pass, fail, success, score, percentage, `all_met`,
confidence or risk — in the response, in the serializer, or in the PWA, asserted in all three.

**The UI distinction that matters most.** `Met` / `Not met` / `Could not verify`, and `unverified`
renders with a *different badge class and a neutral tone* from `not_met` — asserted by parsing the
rendered HTML, not by reading the source. `not_met` is a finding about the work; `unverified` is a
statement about Cofferdam's reach, and rendering them alike would turn every limit of the observer
into an accusation. Reason codes appear as short sentences beneath. A manual criterion shows its
description as the expectation, `Could not verify`, and no control to mark it done.

**No mutation of any kind.** GET only; other verbs are unregistered. There is no rerun route, no
evaluator control and no check-runner control — asserted from the route table and from the shipped
JavaScript, which contains the string `/assessment` exactly once.

**Evidence is named, not copied.** The assessment carries `assembler_version` and
`evidence_input_fingerprint` so a client can correlate with the evidence route, and nothing else from
the bundle. `claim_conflict` is absent entirely: it is a disagreement between records, not a reason a
criterion went unmet, and placing it beside a result is how a reader would come to treat it as one.

**No bridge exposure.** Ten bridge routes, nine authenticated, no assessment Action,
`artifacts_supported` still `false`, `getProjectContext` untouched.

### M2K PR7 — deterministic criterion evaluation and the immutable `EvaluationRecord`

**Merged as `7f21fc4` (#52) and deployed**: workstation and Actions bridge both run it from slot A,
and the live task database is migrated to **schema v8**. The rollback is a **pair** — slot B at
`cd11232` plus the verified pre-v8 schema-v7 backup — because the PR6 runtime refuses a v8 database
outright (measured: `StoreUnavailable` at `TaskStore` first use, at a task read and at `create_app`,
with the main database and both WAL/shm siblings byte-identical afterwards). The record below was
written while it was still on `m2k-pr7-criterion-evaluation`, from the merged `cd11232`, and is kept
as it was written.

PR6 froze **what was required** before dispatch. PR2 to PR5 froze **what machine evidence exists**
for the turn. PR7 is the first PR that answers a question with those two, and it answers exactly one:

    does the stored machine evidence for this exact turn satisfy this exact criterion?

**It is not a task verdict, and there is nowhere for one to live.** No pass, no fail, no aggregate,
no score, no confidence, no risk, no model, no check runner, no command — and no column any of them
could be written into. Six equivalences are forbidden and pinned by tests: `not_met` is not "the task
failed", `met` is not "the task passed", `claim_conflict` is neither, "no criteria" is not success,
and incomplete evidence is not `not_met`.

**Schema v8, additive, two tables.** `task_turn_evaluations` is one row per closed turn per
evaluator version; `task_turn_criterion_results` carries the per-criterion answers. No v7 table
changed shape and the migration writes nothing — it evaluates no historical turn, parses no prompt,
interprets no claim and fabricates no criteria.

**Why not an event, which is where PR5 put its observation.** The difference is *when*. PR5's
committed-range observation is captured while the turn is still open, so it takes an ordinary
sequence inside the turn's own v5 bounds. An evaluation is produced **after** the turn is durably
closed; an event appended then would sit above `closed_through_event_sequence` and belong to no turn,
and moving a closed bound to make room is the exact rewrite bounds exist to prevent.

**The foreign key is the one thing unlike v6 and v7, deliberately.** A baseline and a criteria
snapshot must be durable *before* the turn row exists, so they name `tasks`. An evaluation may exist
only for a turn that has already closed, so it names `task_turns` — making "an evaluation of a turn
that never happened" unrepresentable. It also binds the exact criteria snapshot row.

**Timing, and the crash it survives.** A turn's evidence window becomes final when
`closed_through_event_sequence` is written, in the same transaction as the turn's `completed_at`.
Evaluation runs strictly after that, in a *separate* transaction — so a failure cannot roll back a
task's lifecycle, and the interesting gap is a turn that is closed with no judgement yet. That gap is
not a special case: one function, `evaluate_closed_turns`, runs for one task after a close and for
every task at start-up, and its query excludes anything already evaluated. Restarting ten times
produces one record.

**`EVALUATOR_VERSION = 1`**, distinct from `SCHEMA_VERSION`, `ASSEMBLER_VERSION` and the criteria
model version because those four move for four different reasons. It is in the record, in the
fingerprint, and in the uniqueness constraint — so a future version 2 records its own answer for a
turn without rewriting version 1's.

**Both identities are bound in full**: the criteria `snapshot_id` *and* its fingerprint, the
`assembler_version` *and* the evidence `input_fingerprint`. An id says which durable row was read; a
fingerprint says what content it represented. The evidence bundle itself is never copied in.

**`path_changed` means a resulting observed repository effect**, not "the file was touched at some
instant". Cofferdam observes a boundary, not a process: a worker that edits a file and reverts it
leaves no resulting change and this build cannot see that it happened. That wording is in the module,
the docs and the tests.

**Machine evidence is the only authority.** The evaluator does not read `claims`, `ingestion` or
`relationships` at all — asserted structurally, by scanning the syntax tree for those attribute
names. So a claim cannot satisfy a criterion, a claim's absence cannot fail one, and **incomplete
claim ingestion does not downgrade anything**, which the PR6 readiness audit had floated as a
possible global gate and is deliberately not the rule.

**Closure is predicate-specific and asymmetric.** One attributable observation can prove a change;
absence proves nothing unless every domain it could have appeared in was read completely. Both
domains must close, because a committed change is invisible to `git status` and an uncommitted one is
invisible to the range. A dirty, incomplete or unavailable pre-work boundary blocks **both** a `met` and a
`not_met`. PR4 persists only a coarse boundary word with no path-level detail, so a file that was
dirty before dispatch and restored to HEAD by the worker leaves no observation anywhere — meaning
absence cannot distinguish "never touched" from "was dirty and put back". Only a `clean_complete`
boundary permits either conclusion; everything else is `unverified`, which is a limit of evidence
resolution rather than a statement about the work.

**Domains are never collapsed.** `created` in the committed range and `modified` in the working tree
are two true statements about two moments, and both `path_operation(P, created)` and
`path_operation(P, modified)` are met. A rename is met only on an explicit machine rename record with
both endpoints — **never** inferred from a created plus a deleted.

**`manual` is always `unverified`**, the description is never inspected, and a capability v1 cannot
decide is `unverified` with an unsupported-capability reason rather than an exception or an execution.

**`legacy_unknown` produces no record at all** — a turn that was never asked a question gets no
fabricated zero-result row. **`not_provided` produces a record with zero results and no aggregate**,
which the schema enforces so it can never be totalled up as "everything passed".

**No API surface.** The evaluator is internal: no route, no request field, no bridge Action. Ten
bridge routes, nine authenticated, `artifacts_supported` still `false`, `getProjectContext`
untouched, `ASSEMBLER_VERSION` still **3**.

### M2K PR6 — the immutable per-turn acceptance-criteria snapshot

**Merged as `cd11232` (#51) and deployed**: workstation and Actions bridge both run it from slot B,
and the live task database is migrated to **schema v7**. The rollback is a **pair** — slot A at
`e9f5e26` plus the verified pre-v7 schema-v6 backup — because the PR5 runtime refuses a v7 database
outright (measured: `StoreUnavailable` at `TaskStore` first use, at a task read and at `create_app`,
with the database left byte-identical). The record below was written while it was still on
`m2k-pr6-acceptance-criteria`, from the merged `e9f5e26`, and is kept as it was written.

Five PRs of evidence work left Cofferdam able to say a great deal about what *happened* and holding
nothing at all about what was *required*. There was no acceptance criterion type, no criterion set,
no criterion identity, no criteria fingerprint and no per-turn criteria authority — so "did the work
meet what was asked" had no durable question to be an answer to. PR6 is that question and only that
question. **It evaluates nothing.**

**Schema v7, additive, two tables.** `task_turn_criteria` is one row per **reserved turn** carrying
the criteria state, a server-minted `snapshot_id`, the criteria fingerprint, the criterion count and
a `dispatch_state`; `task_turn_criterion_items` carries the closed structured facts, one row per
criterion. No v6 table changed shape, no row was rewritten, and the migration writes nothing at all.

**The invariant.** A future evaluation must refer to the exact criteria snapshot that was already in
force **before worker dispatch began**. A worker judged against criteria that changed after it
started has been judged against a moving target, and no care in the evaluator repairs that
afterwards. So criteria are a pre-work durable fact, conceptually parallel to PR4's Git baseline,
and frozen by the same event: once `dispatch_started` is durable for a reserved turn, that turn's
snapshot is immutable.

    validate → criteria snapshot → PR4 baseline → `dispatch_started` → **adapter runs**

Both pre-work writes commit before `dispatch_started`, which commits before the adapter call. The
proof is a test in which the adapter, at its **first instruction**, opens a **separate read-only**
connection and finds: the snapshot row, every criterion row, a final fingerprint, the PR4 baseline,
`dispatch_state = dispatch_started` — and **no `task_turns` row**, which is exactly why the snapshot
table's foreign key names `tasks` rather than `task_turns`, the same lesson PR4 recorded.

**Three states, and the difference between the last two is the point.** `present` is an immutable
snapshot with at least one criterion. `not_provided` is Cofferdam durably recording, before
dispatch, that no criteria were supplied. `legacy_unknown` is the **absence of the row**, which is
what every historical turn on this host has, and it is deliberately not writable — the schema
refuses it. A missing row is never read as `not_provided`, and `not_provided` is never read as an
empty criterion set that automatically succeeds.

**Criteria model v1 is small and closed.** Two kinds — `evidence` and `manual` — and three evidence
predicates: `path_changed`, `path_operation` (`created` / `modified` / `deleted`) and `rename`.
Every one is a question the *already stored* claim, artifact, worktree-observation and
committed-range rows can decide in the next PR without any new capture. `manual` means undecidable
by machine, which is neither failed nor passed; a future evaluator returns it as unverified.

**No commands, and not by omission.** There is no shell string, argv, script, test command,
executable path, `check_id` or expression language, and the validator refuses those field names *by
name* with their own reason code. A criterion carrying a command would be dormant execution
authority waiting for a runner. When host-owned named checks with literal argv exist, a future kind
may name one; PR6 does not invent the authority in advance.

**Negative/set criteria are deferred deliberately.** "Nothing changed outside the allowed set S" is
a question current evidence can *sometimes* answer, and the sometimes is the problem: it needs a
bounded structured path set — a third relational layer — and a completeness semantics stronger than
PR2's `machine_complete` or PR4's `status_coverage` establish today.

**Bounds refuse rather than truncate.** At most 32 criteria per turn and 500 characters of
description, and over-bound submissions are refused **before dispatch** with no snapshot written.
This is the one place in M2K where truncation would be wrong in a way it is not wrong elsewhere: a
bounded *observation* is honestly `incomplete`, but a bounded *requirement set* reads afterwards as
the complete list of things the work had to do. Paths go through the same
`normalize_claim_path` / `is_denied_path` doctrine claims and artifacts already use — no absolute
roots, no traversal, no sensitive names, and no rewriting of a path into a safe-looking one.

**Identity is the server's.** `snapshot_id` and `criterion_id` are minted by the store from the same
construction as `task_id`; a submitted one is refused. The **fingerprint** is a domain-tagged,
length-prefixed SHA-256 over the stored criterion facts in `ordinal` order — deterministic, stable
across a restart, and independent of row ids, insertion order, absolute paths, provider/session ids
and every clock. It deliberately excludes the task and turn, unlike `input_fingerprint`: it
identifies *what was asked for*, so two turns given the same requirements share it while each keeps
its own snapshot id.

**Retry is conservative, mirroring PR4.** `AdapterRefusal` records `dispatch_refused` and does
**not** re-open replacement: an adapter's refusal is a statement of intent, not a proof about side
effects, so a retry of the same reserved turn dispatches against exactly the snapshot the first
attempt did. A genuinely new follow-up turn may receive a new snapshot; a message that merely
*resumes* a turn is refused if it carries criteria, because that turn's snapshot is already frozen.

**No public surface.** Criteria enter as an internal keyword-only `TaskService` parameter on
`create_task` and `send_followup`. No route passes it, the `/api/tasks` body allowlist is unchanged,
and there is no criteria route anywhere — asserted from the route decorators' syntax tree.

**No bridge change.** Ten routes, nine authenticated, no criteria Action, `artifacts_supported`
still `false`, `getProjectContext` untouched. **No evaluator, no `EvaluationRecord`, no met/not_met,
no verdict, no risk level, no confidence, no check runner, no command, no provider, no model.**
`ASSEMBLER_VERSION` stays at **3** and the evidence bundle's inputs are exactly PR5's.

**Rollback measured, not assumed.** An isolated v7 database with real criteria content was opened
with the shipped PR5 runtime (`e9f5e26`, schema-v6). It refused with `StoreUnavailable` and the
database file was **byte-identical** afterwards — same SHA-256, same `sqlite_master`, same rows,
`integrity_check` ok, `foreign_key_check` clean. It does create the WAL and shm siblings on its way
to refusing, and writes nothing into them.

### M2K PR5 — committed-work Git observations from the durable baseline

**Merged as `e9f5e26` (#50) and deployed**: workstation and Actions bridge both run it from slot A,
and the live task database is unchanged at **schema v6** because PR5 needed no migration. The
immediate rollback is slot B at `cf29b89` against that same live database; the pre-PR5 backup is
deeper recovery only. The record below was written while it was still on
`feat/m2k-pr5-committed-range`, from the merged `cf29b89`, and is kept as it was written.

PR4 recorded a revision before each turn's worker was allowed to start and consumed none of it. PR5
is the consumption: what the repository gained between that boundary and a stable HEAD observed
after the adapter returned. That is the work PR3 structurally cannot see — once a worker commits,
its changes are *in* HEAD, `git status` is clean, and the clean answer is correct and useless.

**The schema did not move.** It is still **v6**, and there is no migration in this PR. The
observation is persisted as immutable `task_events.evidence_json` on a dedicated code-owned event
type, `committed_range_observed`, which was possible because `EvidenceReference` already carries
`change_kind`, `previous_identifier` and `change_status`, and because a dedicated event gets its own
`MAX_EVIDENCE_ITEMS` budget instead of competing with PR3's status evidence for the same eight
slots. A relational range table would have been a second durable shape for facts the event column
already holds — and, given that an older runtime's `_connect()` runs its `_SCHEMA` before the
forward-version gate, a migration is not something to add without a reason.

**The capture point, and why it is the only one.** `_apply` is the only method that can close a
turn. Both dispatch paths call the capture after the adapter has returned and a real turn row
exists, and before `_apply` runs, holding the service lock throughout:

    adapter returns → turn opens → **PR5 capture** → `_apply` → the turn may close

So the event receives an ordinary Task Core sequence *while the turn is open*, and the v5 bound rule
`opened_after < sequence <= closed_through` attributes it to that turn as arithmetic rather than as
a later decision. Nothing is captured after a close and attached backwards.

**Only for a turn that exists.** Eligibility is `dispatch_state == turn_opened`, written by the
store in the same transaction as the turn row. A refused dispatch, and one that started and never
produced a turn, are left exactly as PR4 recorded them — explicitly uncertain attempts. PR5 invents
no turn for either.

**A revision range is not a history.** `git diff <baseline> <target>` is a *tree comparison*, and
calling its output "what the worker committed" is a claim the command never made. Measured on a real
repository, a baseline recorded on one branch against a target on another reports the other branch's
files as **deleted** — by a worker that deleted nothing; a hard reset backwards does the same. So
the history relation is established first with `git merge-base --is-ancestor`, whose three outcomes
are kept apart: exit 0 is an ancestor, exit 1 is a divergence, exit 128 is an object that could not
be read. A divergence is recorded as `diverged` with **no** diff run and no changes, and exit 128 is
recorded as a missing baseline — collapsing the two would report an unreadable object as a rewritten
history.

**Configuration gets no vote.** Rename detection is a repository setting: with `diff.renames=false`
a move reports as an add plus a delete, which is a different set of machine facts about the same
event. Every behaviour config could change is pinned on the argv — `--find-renames`, `--no-ext-diff`,
`--no-textconv` — the last two also because each can name a **program**, and a probe must not run a
helper the project chose.

**The record grammar is not PR3's.** `git status --porcelain=v1 -z` and `git diff --name-status -z`
are different formats, and assuming otherwise is a silent corruption: porcelain packs `XY` and the
path into one field and emits a rename as **destination then source**, while `--name-status` gives
the status its own field and emits **source then destination**. Reusing PR3's parser would have
swapped the source and destination of every rename while looking entirely correct. Both grammars
were measured against the installed Git (2.53) rather than read from documentation.

**Two observation domains, never merged.** PR3 observes the index and working tree against the
current HEAD; PR5 observes a committed revision range. A path can legitimately appear in both — a
worker commits `foo.py` and then edits it again — and that is two machine facts at two moments, not
a duplicate. Every observation carries its domain, and the relationship group lists the domains that
named each path rather than reducing them to one operation.

That distinction governs conflicts. *Within* a domain a contradiction stands, because those
observations describe one instant against one HEAD. *Across* domains an agreement wins, because they
describe different instants and both can be true: "committed as modified, then deleted" is an
ordinary sequence, and a claim of "modified" was true when it was made.

**A dirty boundary may never contradict.** PR4 records whether the repository was already dirty
before the worker started. If it was, a change that predates the turn can be committed inside the
range and is indistinguishable from the worker's own — so a range whose boundary was `dirty`,
`incomplete` or `unavailable`, or whose history diverged, contributes observed change and **cannot**
produce `operation_agreement = false` or `claim_conflict`. The answer there is `unknown` with an
explicit limitation. Only a `clean_complete` boundary over a valid ancestry is comparison-grade.
Truncation is deliberately *not* one of these conditions: a short path list is about paths that are
missing, and the ones recorded were read exactly — the same line PR3 draws.

**`assembler_version` is 3**, because the bundle now consumes this evidence: eligibility,
relationship resolution and the fingerprint all changed. The fingerprint binds the observation
domain and every assembly-relevant range fact — whether anything was recorded at all, both
revisions, the history relation, the boundary quality, the coverage and the limitation. Assembly
still runs **no Git**: the repository can be deleted after the event is written and the bundle and
its fingerprint are byte-identical.

**A defect found and fixed.** `TaskService._apply` rebuilds every observation reference field by
field before storing it, and that reconstruction was never extended when PR3 added `change_kind`,
`previous_identifier` and `change_status`. Nothing failed — every observation that reached the
database through an adapter simply arrived shaped like a pre-PR3 one, which the assembler correctly
reads as "the operation was never established". The effect was that `operation_agreement` was
permanently `unknown` and `claim_conflict` unreachable on the only path a real task takes; the
store-level tests write evidence directly and never went through it. The three fields are now
carried. The domain is deliberately **not** carried from the adapter but forced to `worktree`: an
adapter setting `committed_range` would be dressing its own report as the host's post-work reading,
which is the same promotion `source` is already gated against.

**Historical compatibility.** The three pre-v5 turns stay `legacy_unknown` with no observations, no
baseline row and no range. Nothing is backfilled and no stored `evidence_json` is rewritten. Every
observation written before PR5 reads as the `worktree` domain, because that is what it is.

**No bridge change.** Ten routes, nine authenticated, no evidence/artifact/claim/baseline/range
Action, `artifacts_supported` still `false`, `getProjectContext` untouched. **No evaluator, no
verdict, no risk level, no confidence, no check runner, no provider, no model.**

### M2K PR4 — the durable per-turn pre-work Git baseline

**Merged as `cf29b89` (#49) and deployed**: workstation and Actions bridge both run it from slot B,
and the live task database is migrated to **schema v6**. The rollback is a pair — slot A at
`d98c10f` together with a verified pre-migration schema-v5 backup. The record below was written
while it was still on `feat/m2k-pr4-git-baseline`, from the merged `d98c10f`, and is kept as it was
written.

PR3's deployment demonstrated the last large machine-observation gap on a live host, and it is not
a parsing problem: a worker may modify files **and commit them**, after which the working tree is
clean and PR3's observation — which is relative to the *current* HEAD — cannot see the work. The
current HEAD is the worker's own commit. What was missing is a revision the machine recorded
**before the worker was allowed to begin**.

PR4 records exactly that and consumes none of it. There is no `git diff baseline..HEAD` anywhere in
this build; deriving committed-work evidence from a stored boundary is PR5, deliberately separate,
because a boundary that is late, wrong, adapter-influenced or silently absent would make every
observation derived from it wrong in a way that looks authoritative.

**The audit that shaped it.** The pre-work guarantee is an ordering claim, so the first question was
where the last host-controlled instruction actually is. On both dispatch paths — `TaskService._start`
and `TaskService.send_followup` — the adapter is invoked **before** the turn row is written, and
deliberately so: an adapter refusal must leave no turn behind, and a follow-up must never be recorded
as delivered before the session took it. The consequence is that the tidy design — write the baseline
inside `_open_turn_locked`, atomically beside `task_turns` — would have persisted the boundary
*after* the worker started, which is the one thing it may not be. It also would have made the honest
outcome "captured, then the adapter refused, so the turn never opened" impossible to represent.

So the capture is its own committed write immediately before dispatch, and the table's foreign key
names `tasks` rather than `task_turns`. Everything else follows the existing discipline.

**Crash semantics, and the correction that shaped the final design.** The first version of this PR
allowed a boundary to be replaced whenever no turn row existed. That was wrong. The adapter is
invoked *before* the turn row is written, so "no turn row" also describes a dispatch where the worker
ran, **possibly committed**, and Cofferdam died before recording the turn — and a retry there would
have read the worker's own commit and stored it as the *pre-work* boundary, destroying the real one
silently, in a way every later observation would inherit.

So permission to replace is its own durable fact, `dispatch_state`, on a dimension separate from
`capture_state`: `captured` / `dispatch_started` / `dispatch_refused` / `turn_opened`.
`dispatch_started` is committed **before** the adapter call, which is what makes `captured` mean
"the adapter had provably not been reached" rather than "no turn row was found". Only `captured` is
replaceable. `dispatch_refused` is recorded — learning the outcome differs from crashing before
learning it — but it does **not** re-open replacement, because `AdapterRefusal` proves nothing about
side effects: `ClaudeCodeAdapter.send_followup` raises it when `send_turn` fails, *after* bytes may
already have reached a live worker's stdin, and the core must not read an adapter's message text to
guess which refusal it got. A retry after a refusal therefore reuses the same reserved turn number
and dispatches against the same boundary — the earliest boundary for a turn number precedes every
attempt at it, which is the property a pre-work line needs. `turn_opened` is written inside the same
transaction as the turn row.

**Schema v6** adds one table, `task_turn_git_baselines`, keyed `(task_id, turn_number)`. Additive:
no column of an existing table moved, changed type or gained a constraint, and no row was rewritten
or inferred. The three historical pre-v5 turns on the production host get **no baseline** — not one
read from the current HEAD, not one derived from a timestamp, not one recovered from the reflog.
`turn_baseline` answers `None`, and `None` means *no boundary was recorded*, never *the tree was
clean*.

**Machine-owned.** The repository root comes from `verify_root` against the host's project registry,
re-verified at dispatch. The adapter, the provider, the task prompt and the API caller cannot choose
the root, the revision, the dirty state, or whether capture succeeded. Every Git argv is a module
constant, so no caller text can become a Git argument, and the stored revision is a resolved object
id — validated as hex of exactly the length `--show-object-format` reports, which refuses `HEAD~5`,
a branch name, a path and every other revspec by construction. The object format is read rather than
assumed: this host runs Git 2.53 and a SHA-256 repository produces 64-hex ids.

**Honest absence.** `unborn` stores no revision and no invented empty-tree object; `not_a_repository`
and `unavailable` store none either. A HEAD that moves across the observation is retried a bounded
three times and then recorded as explicitly unstable rather than resolved to an arbitrary side. A
project that is not a Git repository still runs its task — Git evidence is not a precondition for
somebody's work — but the unavailability is durable before the worker starts.

**The limit, stated rather than glossed.** A clean host-owned snapshot does not prove only the worker
changed the repository afterwards; a person, an editor autosave or another tool can modify the same
tree concurrently. What a stored boundary supports is machine-observed change since a recorded point.
It is not proof of causation and nothing built on it may claim otherwise.

`assembler_version` stays **2** — the bundle's inputs are unchanged — and there is no new HTTP route,
no bridge Action, and no evaluator, verdict, confidence, risk or check runner.

### M2K PR3 — richer machine-owned Git observations

On `feat/m2k-pr3-git-observations`, from the merged `52811dc`. **Merged as #48 (`d98c10f`) and
deployed**; the text below was written while it was still a branch.
PR2 could only ever report `operation_agreement: unknown`, and the reason was not the assembler —
it was that Cofferdam was **already asking Git for more than it kept**.

**What the audit found in the deployed code.** `observe_git` ran `git status --porcelain`, and the
parser then did three lossy things. It sliced past the two `XY` status characters with `line[3:]`,
discarding the operation Git had just reported. It split rename records on `" -> "` and kept only
the right-hand path, dropping the source. And `_safe_relative` refused any path beginning with a
quote — which, in human porcelain, is **every path containing a space, a tab, an arrow or a
non-ASCII byte**, so a file called `has space.txt` produced no evidence at all.

**The probe is now `git status --porcelain=v1 -z --untracked-files=all`.** Git's documented machine format: records are
NUL-terminated, so a newline, tab or literal `->` inside a filename is just bytes, and paths are
emitted raw rather than quoted. The version is pinned explicitly so a future Git changing what
"porcelain" means cannot change what the parser receives. Output is read as **bytes** and each field
decoded strictly on its own — `errors="replace"` would turn a non-UTF-8 filename into a *different*
filename and publish a path that does not exist.

**The `-z` rename order is the reverse of the human one, and that is load-bearing.**

    human : R  tomove.txt -> moved.txt          old first, then new
    -z    : "R  moved.txt" NUL "tomove.txt"     NEW first, then old

A parser written by reading the human output inverts every rename silently, with both paths still
looking plausible. The order is pinned by a test that runs the installed Git rather than by a
comment.

**`GitObservation.changed_paths: Tuple[str, ...]` became `changes: Tuple[GitChange, ...]`**, each
carrying a project-relative `path`, a closed machine `kind`, the raw two-character `status`, and —
for a rename only — `previous_path`. `changed_paths` survives as a derived property so callers that
only want the path set still work.

**The machine vocabulary is closed and Task-Core-owned**: `created`, `modified`, `deleted`,
`renamed`, `unknown`. It lives in `tasks/models.py` rather than in the adapter, because Task Core's
assembler needs it and **may not import from an agent-specific package** — a layer rule the existing
tests enforce, and which caught a first attempt that put it in the adapter.

**`unknown` is a first-class answer, not a failure.** `XY` is a table lookup with no branches:
`??`/`A`/`AM` → created, `M`/`MM` → modified, `D` → deleted, `R`/`RM` → renamed. Everything else
stays `unknown`, each for its own reason — `UU`/`AA`/`DD`/`AU`/`UA`/`DU`/`UD` are **unmerged** and
nobody has decided what happened yet; `T` is a **type change** that none of the four words
describes; `C` is a **copy**, whose source still exists, so calling it a rename would assert a
deletion that did not happen; `MD` is a staged modify then a worktree delete, two true facts that
disagree. A status this build has never seen becomes `unknown` rather than a wrong guess.

**`EvidenceReference` gained two optional fields and needed no schema change.** `change_kind` and
`previous_identifier`, written only when present, so a row carrying no machine semantics serialises
to **exactly** the pre-PR3 key set. The deserializer already used `.get()`, so old rows read back
with `None` — which the assembler treats as "the operation was never established", never as
"nothing happened". `evidence_json` is a TEXT column; **schema stays at v5**, and the rollback pair
established by the PR2 deployment is unchanged. `result` deliberately keeps its old word
`"changed"`, so a client written against the older shape sees a familiar row and ignores the rest.

**The emitter has a real budget now.** The old code emitted `changed_paths[:6]` against a store cap
of `MAX_EVIDENCE_ITEMS = 8`, and a naive 6 → 8 change would have produced 1 HEAD + 8 paths = 9 rows,
of which `_bounded_evidence` **silently drops the last** — an observation lost with no record. The
HEAD row is now counted against the budget, and a **coverage row is emitted every time**, saying
`observed all changes` or `observed some changes`. It is always present because "no coverage row"
and "a build that never wrote one" are indistinguishable to a later reader.

**Truncation is now a real count**, not the old `len(lines) > len(paths)` heuristic that reported
truncation whenever a path was deduplicated or refused. Refused paths are **counted** rather than
dropped in silence — the path itself is not stored, for the reason PR1 stores no rejected payload.
A refused path makes the observation partial, and the bundle reports
`machine_observations_complete: false` with a `machine_observations_incomplete` limitation, so an
`observed_only` absence is read as "possibly not looked at" rather than "looked at and not there".

**Assembler version 2.** `operation_agreement` is now `true`, `false` or `unknown`, answered by one
closed table plus one helper for renames. Agreeing pairs: created/created, modified/modified,
deleted/deleted. Incompatible pairs: created↔deleted, modified↔deleted, deleted↔created,
deleted↔modified. **created vs modified is deliberately `unknown`, not a conflict** — a worker that
creates a file and then edits it truthfully says "created" while Git reports whichever the state
against HEAD supports, and calling that a contradiction would manufacture conflicts out of ordinary
work. Resolution across a group is conservative: one contradiction outweighs a simultaneous
agreement, and anything unestablished leaves the group unestablished.

**A rename is answered by both paths or not at all.** One table cell cannot express "the source and
destination both match", so a dedicated helper compares them: both match → `true`; the machine
renamed the same destination from a **different** source → `false`; the machine saw a rename but
recorded no source → `unknown`. Half a rename proves nothing about the other half.

**The first deterministic `claim_conflict` can now exist** — and the bar is exactly the one PR2
documented: two positive machine facts that cannot both describe one path. Absence is still not
conflict. A legacy observation is not conflict. An unmerged or type-changed path is not conflict. A
truncated observation set is not conflict. `path_agreement` stays **true** for a conflict, because
both records do name the same file; the disagreement is entirely about the operation, which is why
the two are separate fields.

**A conflict is not a verdict.** It does not mean the task failed, the acceptance criteria failed,
or the worker was dishonest. A worker that modified a file and then deleted it produced a conflict
and did nothing wrong. The PWA renders it as **"Records differ"** with the sentence "Both records
are kept as they were", styled `warn` rather than `err`, and the forbidden-vocabulary scan covers
the new screens.

**The coverage limit the audit found, stated rather than papered over.** `git status` compares the
index and working tree against the **current HEAD**. It is not a before/after comparison, because
Cofferdam has no "before": `ClaudeRun` captures no revision at task start, `observe_git` runs once
after a result arrives, and `observation.head` is the commit *at observation time*, recorded as a
pointer and never used as a boundary. **So if a worker commits its work, `git status` reports a
clean tree and Cofferdam observes nothing.** PR3 does **not** add `git diff --name-status`: there is
no durable earlier revision to diff against, and inventing one would be exactly the false
before/after boundary the brief warned about. A test asserts the honest behaviour — the claim stays
`claim_only`, never a conflict.

**Untracked files are enumerated individually.** Git's default reports a wholly new directory as a
**single** record, `?? newdir/` — and Cofferdam's claim model is file-level, so a claim naming
`newdir/a.py` could never pair with it. The observation set would have been silently coarser than
the thing it is compared against, and the mismatch would have read as "the worker claimed a file
Cofferdam never saw change". `--untracked-files=all` is in **literal argv** rather than left to
`status.showUntrackedFiles`, so no user or repository configuration can turn it off: evidence
coverage is not a preference. Nested directories are enumerated to the leaf, and awkward filenames
under them survive.

**Composite `XY` states carry two facts, and both are kept.** `X` is the index against HEAD and `Y`
the working tree against the index, so one status routinely proves **two** things: `RM` is *renamed
and then modified*, `AM` is *added and then modified*, `MD` is *modified and then deleted*.
Collapsing each to one preferred word discards a fact that may be exactly the one reconciling a
worker's claim — and reading that absence as evidence would turn an honest "modified" report after a
rename into a **false conflict**.

So the exact `XY` is persisted as `EvidenceReference.change_status` — optional, bounded to two
characters, still **no schema change** — and agreement is decided against the whole fact set:

* the claim matches **any** proven fact → `true`
* the claim is incompatible with **every** fact → `false`
* anything else → `unknown`

One reconciling fact is enough to stop a contradiction. `RM` + a `modified` claim **agrees**;
`RM` + a `deleted` claim is `unknown` (deleted contradicts modified but not renamed, so not *all*);
`MD` + either `modified` or `deleted` agrees; `MD` + `created` is `unknown`. The simple states still
decide as before: a plain `D` contradicts a `modified` claim, and `created` vs `modified` remains
`unknown`. `change_kind` survives as the primary label a person reads; it is deliberately **not**
what agreement is computed from.

**A rename is never agreed by a status alone.** Even `R ` and `RM`, which prove a rename happened,
do not prove it is *this* rename — same destination from a different source is a different event.
`operation_agreement` returns `unknown` for every rename claim, and only the comparison that uses
both paths may agree one. That is structural rather than a convention.

**The index is not written merely to observe.** `git status` may refresh cached stat information as
an optimisation — a write performed to look. `GIT_OPTIONAL_LOCKS=0` was already in the probe
environment and already passed to the subprocess; PR3 adds no change and instead **proves** it, by
asserting the index file is byte-identical across repeated observations and that no `index.lock`
appears.

**Path safety is unchanged in strength and cheaper.** `_safe_relative` is now purely lexical — the
pre-PR3 version called `.resolve()` and compared against the root, which touches the filesystem to
classify a *string* and resolves symlinks. Classification comes from Git's machine output; nothing
opens a file to decide whether a name is a name. Absolute paths, `..`, drive letters, backslashes,
NUL and control characters are all refused, matching PR1's claim gate so that both sides agree on
what a path is — a tab is refused on both, so an observation of `tab\tname.txt` could never pair
with a claim that structurally cannot exist.

**No bridge change.** Ten routes, nine authenticated, no evidence/artifact/claim Action,
`artifacts_supported` still `false`, `getProjectContext` untouched. **No evaluator, no verdict, no
risk level, no confidence, no check runner, no provider, no model.**

### M2K PR2 — the derived evidence bundle and exact turn provenance

**Merged as `52811dc` (#47) and deployed**: workstation and Actions bridge both run it from slot B,
and the live database was migrated from schema v4 to **v5**. The three historical pre-v5 turns
received **no** inferred bounds and report `legacy_unknown`, exactly as designed. Rollback is a
**pair** — slot A @ `de0e7de` plus the verified pre-v5 backup under
`state/service-backups/m2k-pr2-premigration-20260814-195929/` — because the forward-only schema gate
makes a v4 runtime refuse a v5 database (tested: refused safely, zero mutation). The record below
was written on `feat/m2k-pr2-evidence-bundle` while it was still a branch — where it says "not
merged" or "not deployed" it is describing the moment it was written.
PR1 recorded what a worker said and what Cofferdam saw, and deliberately left them side by side
with nothing noticing they agreed. This is the noticing — and the schema change that makes it
possible to do honestly.

**The `EvidenceBundle` is derived, never persisted.** There is no bundle table, no serialized
bundle column, and no schema version for one. A bundle is assembled on read from facts that are
already durable and already immutable: `task_change_claims`, `task_claim_ingestion`, the
append-only `task_events.evidence_json`, and the new turn bounds. **Assembly re-runs nothing** — no
Git, no filesystem, no provider — which is what makes the result *historical* rather than
*current*: a repository edited after a task finished cannot change what the bundle says about that
task, because none of its inputs live in the repository. A future `EvaluationRecord` refers to a
snapshot by `(task_id, turn_number, assembler_version, input_fingerprint)` rather than copying it.

**Schema v5 exists for one reason: exact turn attribution cannot be reconstructed from v4.** A
claim carries an exact `turn_number`, an event carries an exact `sequence`, and `task_turns`
carried neither end of the sequence range it owns. The only v4 bridge between them was a pair of
timestamps, and **timestamps are not an authoritative shared boundary** — two events can share a
millisecond, and the call that writes `started_at` is not the call that allocates the sequence. So
v5 adds one additive table, `task_turn_bounds`, holding
`(task_id, turn_number, opened_after_event_sequence, closed_through_event_sequence)` with a real
composite foreign key to `task_turns(task_id, turn_number)` and CHECK constraints for
`opened_after >= 0` and `closed_through IS NULL OR closed_through >= opened_after`.

**The bounds are written inside the turn-lifecycle chokepoints, in the same transaction.** There
are exactly two turn-open call paths and both funnel through `_open_turn_locked`; there is exactly
one close path and it funnels through `_close_turn_locked`. Both read `tasks.event_cursor`
themselves rather than accepting one, because a cursor read a moment earlier is a cursor another
event may already have moved. A closed turn owns
`opened_after < sequence <= closed_through`; an open turn owns everything above its floor.
`opened_after == closed_through` is a **valid** turn that owned no events. The transition event is
appended before the turn closes, so it belongs to the turn it ended, and a follow-up that closes
turn N and opens turn N+1 in one transaction does both at the same cursor — `(…, X]` and `(X, …]`,
adjacent and never overlapping.

**Historical turns get no bounds, and no guesses.** Production's three pre-v5 turns receive **zero**
inferred rows: nothing derived from `started_at`, `completed_at`, event timestamps, event types,
the nearest sequence or the task's state. They report `turn_attribution = legacy_unknown`, carry
their own claims (which have a durable turn number), and receive **no machine observations at all**
— a legacy turn shown task-wide observations would be a turn-scoped claim built from task-scoped
evidence, which is the exact falsehood this milestone exists to prevent.

**Path agreement is not operation agreement, and neither is a verdict.** Today's machine
observation is `git status --porcelain` reduced to *this project-relative path appears in the
changed set*. That proves the path changed; the porcelain status letters are not in the durable
record, so it proves nothing about *what* was done. A claim of `modified src/foo.py` matched to an
observation that `src/foo.py` changed yields `relationship = path_agreed`, `path_agreement = true`
and `operation_agreement = unknown`. The vocabulary is `path_agreed`, `claim_only` and
`observed_only` — deliberately never a bare `agreed`, because an unqualified word invites a reader
to supply the qualification and they will supply the strongest one. `claim_only` means unmatched
and unverified, **not** false or dishonest; `observed_only` is not evidence of concealment and is
published next to the claim-set completeness that determines how to read it.

**No `claim_conflict` is emitted, and that is a finding rather than an omission.** To call a claim
and an observation incompatible, the observation would have to carry semantics like "this path does
not exist" or "this path was created, not modified", and no supported observation carries either.
Absence is not conflict. PR2 emits zero conflict relationships; the place a future observation type
would plug in is marked.

**The three `git_observed` shapes are distinguished by name.** The adapter emits a path change
(`evidence_type=file`, `operation="git status"`, `result="changed"`), a HEAD/commit observation
(`evidence_type=commit`, `operation="rev-parse HEAD"`, identifier a twelve-character hex commit id)
and a clean-tree statement (`evidence_type=artifact`, `operation="git status"`, `identifier=None`).
Only the first participates in path matching. A matcher that accepted any non-empty identifier from
a `git_observed` reference would compare a **commit id to a claimed filename**, and one that keyed
on `operation` alone would confuse the path shape with the clean-tree shape, which share it. An
unrecognised `git_observed` shape becomes a bounded `unsupported_observation_shape` limitation
rather than being fabricated into a path observation.

**Claim-set completeness has four states, and the fourth is the honest one.** `complete`,
`incomplete`, `legacy_unknown`, and **`ingestion_missing`**. The last exists because PR1's write
path genuinely produces it: `TaskService._record_change_claims` returns without writing anything
when the adapter reported no claims, when the task's project is gone or disabled, and when the
project root fails re-verification — three different facts leaving the same absence. Calling that
`complete` would be a claim the record cannot support. Several ingestion rows in one turn are
aggregated deterministically: counts summed, `truncated` true if any, reason counts merged by
integer addition, and row identities preserved so the fingerprint binds to *which* rows were
aggregated rather than only to their totals.

**Relationships are grouped by path, not by pair.** Six duplicate claims and six duplicate
observations of one path produce **one** group carrying both source lists, not thirty-six rows — a
combination is not a fact anybody recorded. Every source identity is preserved; the lists are
capped, and a cap that bites emits `relationship_sources_truncated` rather than silently shortening.
A rename is represented as **two** semantic targets. Both observed means both `path_agreed` with
`operation_agreement = unknown` — two paths changing is not proof they changed into each other.
One observed leaves the other `claim_only`, which is a gap and **not** a conflict.

**`input_fingerprint` is a domain-tagged, length-prefixed SHA-256** over exactly the immutable
inputs assembly used, following `mind/hashing.py`'s discipline with its own `v1` tag.
`assembler_version` is separate from the bundle's `version`, because "can I parse this" and "was
this produced by the same rules" are different questions. **Project-relative semantic paths are
inputs** — `src/foo.py` becoming `src/bar.py` is a different statement about different work, and a
fingerprint that ignored it would call two different claim sets identical. **Absolute host paths
are not**: no project root, no `/home/...`, no slot path, no filesystem authority, and none of them
is an input to assembly in the first place. Neither are provider or session identifiers, read time,
live Git state, or artifact preview bodies. An open turn's value legitimately moves when a new
eligible event lands, because its input set has genuinely grown; a closed turn's does not, and an
event outside the window never reaches the hash.

**One private route.** `GET /api/tasks/{task_id}/turns/{turn_number}/evidence`, guarded by
`require_token` — the **device token only**. The Actions bridge reads ten task routes with its own
credential and this is not an eleventh, and it is refused because `require_token` has never heard of
that credential rather than because a check rejects it. Turn-qualified, because a task-level
endpoint would have to merge turns or pick one, and merging turns is precisely what v5 exists to
stop. `GET` only, no root or path selector, no policy selector, no artifact body, no filesystem
read, no Git execution, no provider call, bounded serialization. `generated_at` sits on the
envelope as labelled presentation metadata, never inside the bundle and never in the fingerprint.
Repeated reads create zero events, turns, claims, artifacts, ingestion rows and bounds, and do not
touch `updated_at`, `lifecycle_revision` or `event_cursor`.

**The PWA panel is read-only and its vocabulary is narrow on purpose.** Five sections — worker
claims, machine observations, relationships and gaps, claim ingestion, turn attribution — with
"Path agreed", "Claim only", "Observed only", "Operation not established", "Claim set incomplete"
and "Legacy turn attribution unavailable". **No PASS, FAIL, SUCCESS, TRUSTED, LYING, confidence or
risk level**, asserted against the shipped file with comments stripped. "Operation not established"
is printed for every group **including the agreeing one**, because "Path agreed" is the row most
likely to be read as "verified". There is no mutation control in the section: no button, input,
form, textarea or select.

**The Actions bridge is unchanged.** No new operation, no evidence Action, no artifact Action, no
claim Action; `artifacts_supported` stays `false`; `getProjectContext` is untouched.

**Schema rollback consequence, recorded rather than solved.** The forward-only gate is unchanged: a
schema-v4 runtime opening a v5 database refuses it, which is correct. So after an eventual v5
deployment, rolling back needs a prior compatible runtime **and** a pre-v5 database backup
restored. Backwards schema compatibility is deliberately not attempted here.


### M2K PR1 — adapter-reported change claims and the artifact foundation

**Merged as `de0e7de` (#46).** It was deployed from slot A until the PR2 deployment moved both
services to slot B; slot A is now the rollback runtime.
The record below was written on `feat/m2k-pr1-change-claims`, from the merged `9fcbc8f`, while it
was still a branch — where this entry says "not merged" or "not deployed" it is describing the
moment it was written.
The claim side of evidence, and nothing else: Cofferdam could already record what it *observed*
(`git_observed`, `os_observed`, `cofferdam_action`) and had no structured way to record what a
worker *said it did*.

**Two records, not one.** A `ChangeClaim` is a statement — "I modified `src/foo.py`" — and is
`adapter_reported` by construction: `source` is a constant on the dataclass and there is no field
on the adapter's submission to set it. An `ArtifactRecord` is what Cofferdam saw when it opened
that path itself, and every field on it is `os_observed`. They are separate tables because
D-2026-08-11-6 says every field carries its source kind, and one row holding both a claimed path
and a Cofferdam-computed SHA-256 would need one provenance for two different kinds of statement.

**Nothing is compared.** A claim naming the same path as a `git_observed` event sits beside it and
is not marked verified, not cross-referenced, not counted as agreement. That is PR2's, and doing it
at record time would let a claim become believed as a side effect of arriving next to an
observation. There is no verdict, no confidence, no risk level and no column for one.

**The deny policy covers conventions, on both sides of a rename.** Credential directories
(`.ssh`, `.gnupg`, `.aws`, `.docker`, `.kube`, `.cofferdam`, `secrets`), known credential
basenames, `.env` variants and credential extensions (`.pem`, `.key`, `.p12`, `.pfx`, `.jks`,
`.keystore`, `.p8`, `.tfstate`, `.env`), with one backup extension stripped once so
`private.pem.bak` is denied and `notes.md.bak` is not. It recognises conventions rather than
scanning content, so `docs/environment.md`, `src/tokenizer.py`, `docs/secrets-design.md` and
`config/database.example.yml` stay readable — a scan of all 418 tracked repository files denies
none of them. **A rename is checked on both source and destination**, so a sensitive destination
cannot become a way to store bytes through a harmless-looking source; either side denied withholds
the artifact while keeping the claim.

**Claim ingestion is bounded, and the loss is durable.** Both limits and every deterministic
validation refusal are counted into `task_claim_ingestion` — submitted, accepted, rejected, a
truncation flag and counts by closed reason code — written in the same transaction as the claims
they describe. **No rejected payload is stored**: there is no column for a refused path, operation
or label, because a refused path may be an absolute location, a traversal attempt or a credential
file name. A future `EvidenceBundle` can therefore tell a *complete* claim set from an *incomplete*
one after a restart, without any of the refused material having been kept. It is bookkeeping, not
evaluation: no verified, passed, matched, confidence or risk field exists on it. A rejected claim
and a valid claim whose bytes could not be read stay different facts — the second is still a stored
claim carrying its artifact reason.

**Schema v4, additive.** Three new tables — `task_change_claims`, `task_artifacts` and
`task_claim_ingestion` — created by
the same `CREATE TABLE IF NOT EXISTS` script every start already runs. No existing column moved,
changed type or gained a constraint; `task_events.evidence_json` is untouched and a v3 task reads
back byte-identical. A task from before the tables simply has no claims.

**Containment and the deny list happen at record time** (D-2026-08-09-3). The root comes from the
task's project through the host-owned registry and is re-verified with `verify_root` at the moment
of recording — never from the adapter, never from a value cached when the task started. Resolution
reuses the Mind subsystem's descriptor-relative walk, so a symlink at any component is a refusal
from the kernel rather than a comparison made afterwards. A code-owned secret-path deny list is
applied **before anything is opened**, so denied content never enters the store and cannot be
served later by a surface nobody has written yet. The claim itself is still recorded — "the worker
said it changed `.env` and Cofferdam refused to look" is an auditable fact.

**Digest and size are machine-observed.** A domain-tagged, length-prefixed SHA-256 over exactly the
bytes Cofferdam read, following `mind/hashing.py`'s discipline with its own tag. There is no
partial digest: a file above the read cap records `artifact_too_large` and no digest at all, because
a hash over a prefix is not a hash of the file.

**The preview is the only file content that enters the database** — a bounded prefix of an
allowlisted text type, decoded strictly, or nothing. Binary bytes are never decoded with
replacement and called a preview.

**No surface.** No route, no bridge Action, no artifact download or preview endpoint, and
`artifacts_supported` stays `false` with its current reason — Task Core being able to store records
is not the same as there being a safe consumer for them.

**Adapter integration is deliberately narrow.** `AdapterOutcome` gains `change_claims`, carrying
submissions with a closed operation vocabulary and a project-relative path — and no `claim_id`, no
`artifact_id`, no `digest`, no `verified` flag, no root, and **no command field of any kind**
(D-2026-08-11-7). Only the validation adapter reports one, because it is code-owned and
deterministic. The Claude Code adapter observes with Git and has no structured claim source; the
Agent SDK adapter's normalizer deliberately never reads a tool input, and PR1 does not change that.
Neither is wired, so real agent tasks record zero claims — which is the honest result rather than
prose parsed into invented claims.

**One defect fixed in passing.** `mind/documents.py` opened a resolved target `O_RDONLY` without
`O_NONBLOCK`, which **blocks forever on a named pipe** — harmless while every path came from
host-owned configuration, and reachable once the same resolver took an adapter-claimed path. Fixed
at the source rather than worked around in the caller.

## Assessment aggregation doctrine (M2K PR9 — design only, nothing implemented)

**Merged as `b2314f0` (#54).** Documentation only, so there was no deployment step and production was
untouched. PR10 then persisted the one prerequisite this doctrine named — criterion continuity — and
is on a branch; see *In progress* above. Everything below is still design: **no aggregate exists.**

Nothing described in this section exists in code. It is the contract a future aggregate must obey,
settled deliberately **before** the named check runner adds another mechanism that produces results.
The decisions are D-2026-08-16-2 through D-2026-08-16-6; the reference text is in
[`docs/AGENT_TASK_CORE.md`](docs/AGENT_TASK_CORE.md).

**Three axes stay separate, and this is the load-bearing rule.** *Worker lifecycle* answers **what
happened to execution** (`completed`, `failed`, `interrupted`, `cancelled`). *Acceptance* answers
**what the recorded criteria and evidence establish**. *Verification reach* answers **how far
Cofferdam could see**. A task whose lifecycle is `completed` has not thereby met its criteria — the
live database is the proof, with 10 completed tasks and zero evaluations — and a task whose lifecycle
is `failed` may still have met every criterion recorded for its turn. Merging these would let a
compliant worker declare its own success, which is the single failure this whole milestone exists to
prevent.

**Two dimensions per turn, not one overloaded enum.** *Availability* is `assessable` or
`not_assessable`, derived from the criteria state alone: `present` → assessable; `not_provided` →
not assessable, reason `no_structured_criteria`; `legacy_unknown` → not assessable, reason
`historical_criteria_unknown`. Neither absent case is ever `met`, `success`, `passed` or an empty
pass. *Acceptance outcome* exists **only** when criteria are `present`, and its closed V1 vocabulary
is `met` / `not_met` / `incomplete`.

**The composition rule, in precedence order.** Any deterministic `not_met` ⇒ **`not_met`**, because
one known unmet requirement is already enough to know the turn's recorded requirements were not all
established. Otherwise any `unverified` ⇒ **`incomplete`**, never `not_met` — this preserves the
existing doctrine that evidence limitation is not failure. Only when every criterion is `met` ⇒
**`met`**. Known failure dominates; uncertainty blocks `met`; nothing else yields `met`.

**`met` is narrow on purpose.** It means *the acceptance criteria recorded for this turn are all
established as met by the current assessment model.* It does not mean the task succeeded, the worker
succeeded, the user's full intent was captured, or that a later turn cannot regress it.

**Manual criteria cannot currently reach `met`.** A `manual` criterion always evaluates to
`unverified` because no human-answer channel exists, so **any `present` snapshot containing one is
capped at `incomplete`**. That is the honest state and it is not to be worked around: manual
completion must never be inferred from worker prose, a PWA interaction, a claim, or a model
judgement. A human-answer channel is new authority and new state, and needs its own reviewed design.

**Blockers are context, not a competing outcome.** `requires_human` must not become a fourth
aggregate value — doing so would hide machine incompleteness behind human incompleteness whenever
both are true. The recommended shape is orthogonal boolean context beside the outcome
(`requires_human`, `machine_verification_incomplete`), so a turn can say `incomplete` *and* say
exactly why, and a caller can compose the two without guessing which one was suppressed.

**`claim_conflict` stays out of aggregation entirely.** It is a disagreement between an adapter's
record and the machine's, not a criterion result, not a task failure and not an aggregate blocker.
It remains evidence and audit context on the evidence surface, where a person looks at it.

**There is no task-level aggregate, and that is the decision.** Per-turn acceptance is well defined
by the above. Task-level acceptance across multiple turns is **unavailable** until criterion
continuity semantics exist, because both obvious rules are demonstrably wrong. *Accumulate all turns*
is unsafe: turn 1 requires `foo.py` created, turn 2 requires it removed, and treating both as
simultaneously active makes the task's own requirements contradictory when the second was simply a
deliberate change of mind. *Latest turn only* is unsafe in the opposite direction: turn 1 requires
feature X plus tests, turn 2 adds logging, and honouring only turn 2 silently drops X and the tests
from acceptance. Cofferdam persists no fact saying whether a later snapshot replaces, extends,
narrows, supersedes or is independent of an earlier one, so **no task-level rule can be correct
today**, and inventing one is worse than reporting the gap.

**What continuity will need** (design only when written; **M2K PR10 implements exactly this** —
explicit modes, snapshot-level predecessor plus criterion-level relations, planner/user authority,
frozen pre-dispatch, additive schema v9): continuity must be
**explicit** rather than defaulted, because a wrong default is silently applied to every task;
`replace` and `extend` are not distinguishable by inspection. It likely wants a snapshot-level
relation such as `supersedes_snapshot_id` **plus** criterion-level relations, because a later turn
routinely supersedes one requirement while leaving its siblings live. Content fingerprint matching is
**not** sufficient — identical text does not prove lineage, and differing text does not disprove it.
The authority is the **planner or the user**, never the worker and **never the adapter**: a worker
that could declare its new criteria supersede its old ones could retire the requirement it just
failed. Continuity must be frozen **pre-dispatch alongside the criteria snapshot**, for the same
reason the criteria are: a boundary a worker can move after seeing its own results is not a boundary.
It would require an additive schema version. It is a **prerequisite** for any runtime task-level
aggregate.

**A future aggregate carries its own version.** `AGGREGATOR_VERSION` (or equivalent) must be
code-owned and independent of the schema, assembler, criteria-model and evaluator versions, because
a change of composition doctrine must not silently reinterpret aggregates recorded under the old one.

**Derived on read, not persisted — recommended.** A per-turn aggregate is a pure deterministic
function of an immutable `EvaluationRecord` and its criteria snapshot, so persisting it stores
nothing that cannot be recomputed, while adding a write path to a surface whose central property is
that reading it mutates nothing. Deriving it keeps the no-mutation-on-GET doctrine intact and makes a
doctrine change a re-render rather than a migration. If historical audit later requires "what did we
say at the time", that is an argument for persisting the *aggregator version alongside the answer*,
not for persisting the answer alone — and it should be taken as its own decision.

**Ordering: doctrine first, runner second.** The named check runner introduces the first
project-scoped command execution authority, a new recorded result type, probably a new criterion kind
and `check_id`, invocation and result persistence, timeout and output policy, and an evaluator
semantic expansion that would move `EVALUATOR_VERSION`. Built before this doctrine, all of that would
produce results feeding an undefined consumer. Its trust boundary is unchanged and still binding
(D-2026-08-11-7): host-owned definitions by stable id, literal `argv`, `shell=False`, validated
project `cwd`, bounded timeout, bounded output, off by default per project, and **neither the caller
nor the adapter ever supplies command text**.

## M2J records — the egress boundary and the read surface (written while each was on its branch)

M2J is complete; see *M2J closeout* above.

### M2J PR4 — read-only project-context surfaces

**Merged as `44e4994` (#44) and deployed**: workstation and Actions bridge both run it from slot B,
with slot A at `5afaa8e` (PR3.5.1) retained as the rollback. The record below was written on
`feat/m2j-pr4-project-context-read`, from the merged `5afaa8e`, while it was still a branch —
where this entry says "not merged" or "not deployed" it is describing the moment it was written.
The first milestone that may expose project context outside this host, and it consumes the PR3.5
boundary rather than reopening it.

**Two routes, one object.** `GET /api/projects/{project_id}/context` on the daemon and
`getProjectContext` on the Actions bridge both return a serialized `CloudContextProjection`.
`serialize_project_context` refuses anything else **by type** — a `LocalContextPack` duck-types past
a looser check, because it also has `to_dict`, `version` and `parts`.

**`project_id` is not workspace selection.** It resolves to the one enabled workspace naming that
project, and that workspace must be the active one; otherwise `workspace_not_active`. Zero matches,
several matches, a disabled workspace and a disabled project are four more distinct refusals. There
is no "pick the first one" anywhere in the path.

**The pack is built with no user message** (D-2026-08-13-5) rather than with a fake one.

**Bridge scope.** A third dependency, `require_context_caller`, admits the device token or the
bridge credential on this one GET. It is deliberately *not* a reuse of `require_task_caller`:
sharing one would mean a later task route silently gaining context authority, or a later context
change silently reaching the task surface. The bridge still cannot read Mind, mutate Working
Context, activate a workspace, create a task or touch the filesystem — asserted by tests that
present the real credential to seven routes and get seven 401s.

**Bounded transport.** 128 KiB serialized ceiling, refused rather than trimmed (`response_too_large`),
on top of the unchanged 16 KiB content budget.

**Read-only, and PR4 owns no mutation.** No `syncWorkspace`, no objective editing, no workspace
switching, no proposal, no task. Ten consecutive reads leave Working Context, the objective, the
task list and the filesystem byte-identical.

**PWA.** One panel, two labelled columns — local host-only state beside the cloud-safe projection —
so the boundary is visible rather than implied. It shows the policy id, budget usage, projected
parts behind a disclosure, omission counts by reason, and the projection's own carried limitations.
It makes no "secret-free" claim.

### M2J PR3.5.1 — projection sanitizer hardening

**Merged as `5afaa8e` (#43) and deployed**; it is now the rollback slot's release. The record below
was written on `feat/m2j-pr351-projection-sanitizer-hardening`, from the merged `c24be24f`, while it
was still a branch — where this entry says "not merged" or "not deployed" it is describing the
moment it was written. Two recognition-layer defects found by PR3.5's post-deployment validation.
Sanitizer and documentation only: no schema change, no policy-id change, no source-allowlist,
Global Mind, Working Context, budget, candidate-model, type-boundary, persistence or logging
change, no route and no OpenAPI edit.

**Neither defect was ever externally reachable.** Nothing under `cofferdam/` imports the
projection package, so PR3.5 shipped the egress boundary with no surface able to call it. That is
why these are recorded as defects fixed before exposure rather than as an incident.

- **Bare credential assignments were not detected.** `_ENV_ASSIGNMENT` opened with a mandatory
  character, so `COFFERDAM_ACTIONS_TOKEN=` matched while a bare `TOKEN=`, `API_KEY=`, `APIKEY=`,
  `SECRET=`, `PASSWORD=`, `AUTH=` or `PRIVATE_KEY=` did not. The prefix varies between hosts and
  the keyword carries the meaning, so requiring the prefix inverted which half mattered. The
  shipped adversarial suite passed because its one positive case used a prefixed name. The prefix
  is optional now; the value test is untouched, so `API_KEY=xxxxx` and `TOKEN=<your-token>` are
  still documentation.
- **A doubled slash bypassed every path rule.** POSIX collapses a run of separators, so
  `/home//x` names what `/home/x` names, and the patterns accepted exactly one slash. The known
  host literals were worse: a substring test cannot see a separator the operator did not type, so
  a caller that named a root still emitted it with a doubled separator inside. Separators are runs
  now, in both the generic patterns and the literals.

**The fix is bounded, and its own cost was measured.** Accepting a run reintroduced the quadratic
backtracking PR3.5 already paid 84 seconds for once — a long slash run cost 0.85 s per known root
— so the runs are anchored at both ends (`(?<!/)/+(?!/)`), which is the portable spelling of an
atomic group on Python 3.9. Sanitization is linear again and the regression is asserted by tests
that compare growth rather than a wall-clock threshold alone.

**Honesty preserved, not widened.** Whole-part fail-closed omission is unchanged, and no
limitation was quietly dropped. One was *added*: credential variable names are matched in upper
case only, so `api_key=` in lowercase prose is not detected. That was always true and was hidden
behind the larger gap; lowering the case would widen a rule whose consequence is dropping a whole
eligible part, so it is recorded rather than fixed by reflex.

**Also in this PR:** D-2026-08-13-4 resolves the `syncWorkspace` double-booking — the record had
it under both M2J PR4 and M2M. `get_project_context` is PR4's read surface; `syncWorkspace` is
M2M's, because it mutates and the egress policy authorizes no mutation. PR4's hard gate is
unchanged.

### M2J PR3.5 — `CloudContextProjection` and the egress boundary

Merged as `c24be24f` (PR #42) and **deployed**: workstation and Actions bridge both run it from
slot B, with slot A at `31ab1149` retained as the rollback. Documented in
[`docs/CLOUD_CONTEXT_PROJECTION.md`](docs/CLOUD_CONTEXT_PROJECTION.md) and decided in
D-2026-08-13-3. The record below was written while it was on
`feat/m2j-cloud-context-projection`, from the merged `31ab1149`.

**Post-deployment validation found two recognition-layer defects**, fixed by PR3.5.1 above. The
type boundary, Global Mind exclusion, Working Context allowlist, budget accounting and
zero-side-effect properties all validated clean against the deployed code.

**The second of D-2026-08-11-5's two security objects now exists, and PR4 is gated on it.** PR3
built the rich local pack. This PR builds the bounded object that a later authorized surface may
send, and the gap between them is deliberate: a `LocalContextPack` is not structurally
cloud-authorized, and there is no method on it, and no helper anywhere, that turns it into one.
The only route is `ContextProjector.project`, under the named profile
`project_context_external_v1`.

**Eligibility is decided on the reference, not the kind.** `global:preferences` and
`project:cofferdam:status` are both `memory`, so a policy keyed on `source_kind` would have
published personal memory the first time somebody wrote the obvious condition. The projector
decomposes the semantic reference — scheme, identity, role — and then requires the kind to
*agree*; disagreement is its own refusal rather than a fallthrough.

**Allowed:** project `status`, `plan` and `decisions` for the pack's own project, and four Working
Context fields — objective, expected next step, plan checkpoint, pending decision — projected from
PR3's structured `fields` and **never** from its rendered text, which already contains
`delegated worker:` and `active task:` lines.

**Denied by default:** all four Global Mind roles, **including the `communication_style` and
`preferences` that are in every pack on the production host**; the current user message; `design`;
every other project and workspace; the evaluation slot; and every scheme the profile cannot
classify. Sentinels in all four vault documents are searched for in the whole serialized
projection, so the proof is about bytes rather than about structure.

**Content is sanitized, not just metadata.** PR3's production validation established that
canonical Markdown legitimately contains `slots/a`, vault roots and operational paths, so a clean
`source_ref` proves nothing about the text beneath it. Recognised local paths are replaced with a
visible placeholder and the transformation is declared on the part; credential-shaped material
omits the **whole part** rather than being rewritten, because a lossy edit of a possible secret is
a guess that is permanent when wrong. URLs and API routes such as `/api/tasks` are deliberately
not treated as filesystem authority.

**No claim that pattern matching is security.** The protection is layered — narrow source
allowlist, Global Mind excluded by default, structured field allowlist, semantic reference
grammar, known-host-value redaction, conservative secret detection, fail-closed omission, byte
bound — and only two of those steps are recognition. The residual limits are carried on every
projection and asserted as passing tests, so they are recorded behaviour rather than a caveat.

**Its own budget:** 16 KiB of UTF-8, a quarter of the pack's 64 KiB and deliberately a different
number, with per-slot caps and exact accounting. Nothing is dropped silently: every part of the
pack is either projected or carries an omission row with a closed reason code.

**One defect found and fixed in this PR.** Three sanitizer patterns had an unbounded character run
before a required literal, so a long token-free line backtracked from every start position and one
large canonical document turned a projection into an 84-second operation. The runs are bounded and
a regression test asserts the behaviour is not quadratic.

**Nothing leaves the host, and nothing here could make it.** No HTTP route, no Actions bridge
operation, no OpenAPI change, no PWA surface, no provider client, no model, no retrieval and no
persistence. A test asserts the package imports nothing that could send anything, another projects
a pack with `socket` monkeypatched to raise, and another asserts the projector's entire public
surface is one method named `project`. Route surfaces and the OpenAPI document are byte-for-byte
unchanged from `31ab1149`.

**Not in this PR:** any transport or surface (PR4); authentication, authorization or a destination
contract, which a surface owns and projection deliberately does not; evidence and evaluation
(M2K); the planner and any model runtime (M2L); the dashboard (M2M); embeddings, vectors and
retrieval (M2N). No workspace policy override permitting selected Global Mind extracts — allowed
later by D-2026-08-11-5, not built here, and today's default stays exclusion.

## M2J records (written while each was on its branch)

### M2J PR3 — the Context Builder and `LocalContextPack`

Merged as PR #41, squash-merged as `31ab1149`, and deployed to slot `a`; the rollback slot holds
PR2.1 (`f279fc2`). **Implemented and validated locally and against an isolated runtime before
merge**, and the record below was written while it was on `feat/m2j-context-builder`. Documented in
[`docs/CONTEXT.md`](docs/CONTEXT.md).

**Cofferdam can now assemble bounded local context deterministically, with no model anywhere in
the path.** Given the current user message and the host's current state, the builder produces a
`LocalContextPack`: an ordered list of typed parts, each carrying
`{source_kind, source_ref, observed_at}` and how it was selected, plus the budget it was built
under and **one row for every source that is not in it, with a reason**. It reads Working Context
through `WorkspaceService` and memory through `MindService` **by role**, and adds no authority of
its own — no path, no root, no reader, no store.

**The pack is a value, not a record.** Nothing persists it, caches it, indexes it or reuses it,
and no database, directory or file is created by building one. A restart is irrelevant to this PR
because it introduces no durable state at all.

**Nothing leaves the host, and there is no code path that could make it.** No provider client, no
bridge Action, no worker context, no serializer to a wire format, no prompt, no message array, no
system string, no template. `CloudContextProjection` (D-2026-08-11-5) still does not exist, and a
test asserts the package imports nothing that could send anything — no `socket`, `urllib`,
`http.client`, `httpx`, `requests`, `subprocess` or provider SDK — while another builds a pack
with `socket` monkeypatched to raise.

**The budget is UTF-8 bytes, and that is a decision rather than a default.** `DECISIONS.md`
requires a bounded pack and names no unit. A token count would have been wrong twice: it would
make a model-free component depend on a provider's tokenizer, and it would make the same pack cost
different amounts on different models. Bytes are defined by the encoding, identical on every host,
and already this repository's unit for a document. 64 KiB total by default, with per-source
ceilings — Working Context 4 KiB, status 8 KiB, plan 12 KiB, decisions 12 KiB, each global extract
4 KiB — so a 120 KB `DECISIONS.md` cannot crowd out everything after it, and the accounting is
enforced in one place and asserted to equal the sum of the parts.

**The current message is never trimmed.** If it alone exceeds the budget the build **refuses** and
returns **no pack** — before a single document is read, so an oversize message never causes memory
to be touched. Trimming would show a planner a sentence the person did not write, which is the
rule the workspace objective and the memory-proposal reason already follow; a partial pack would
present the incomplete thing as complete; and summarising it would need the model this milestone
does not have.

**Selection is explicit or structural, and each part says which.** An `explicit` part is a section
a **person** named through `plan_checkpoint` or `pending_decision_ref` — the PR1 fields that were
recorded as opaque with nothing resolving them, and this is the reader they were reserved for.
Matching is literal equality on a slug; there is no fuzzy matching and no scoring. A reference that
names no section **omits the role** with `explicit_section_missing` and puts **no structural
substitute in its place**, because a substitute would answer a question nobody asked and would look
identical afterwards. A `structural` part is whole sections taken by position — from the top, or
from the **end** for `decisions`, which is how "recent decisions" is implemented without any
judgement about content.

**No relevance is claimed that is not real.** D-2026-08-12-4 makes semantic retrieval a *required*
Mind capability, and requiring it is exactly why it is not approximated: a keyword heuristic
labelled "relevant" would be believed by everything downstream and would be wrong invisibly. The
M2N seam is one typed parameter — `build(..., candidates=[RetrievedCandidate(...)])` — validated,
ordered and budgeted like everything else. **Nothing in this build supplies a candidate**, and a
test exercises the seam so it is a boundary rather than dead code.

**The evaluation slot is empty and says so.** Priority position six is the latest evaluation
summary, M2K does not exist, and every pack therefore carries an omission row for
`evaluation:latest` with the reason `source_not_in_this_build`. No evaluator was written to fill a
priority position and nothing is fabricated to stand in for one.

**Global mind is two roles, not four — and that is now a decision** (D-2026-08-13-2, which closes
OQ-5). **Read authority is not context inclusion**, and neither is inclusion egress permission:
three separate questions — *may Cofferdam open this*, *should this be in this pack*, *may this
leave the host* — and none implies the next. `communication_style` and `preferences` are
automatically eligible; `user` and `cross_project` are **not automatically injected while granted,
mapped and readable on the production host**. A pack should carry context appropriate to the
current interaction rather than every piece of locally accessible memory; `USER.md` may hold broad
personal information irrelevant to most requests; `CROSS_PROJECT.md` pollutes a pack badly when the
active workspace concerns one project. Tests assert that granting all four roles does not widen the
pack, that the read of an excluded role genuinely succeeds (so the exclusion proves something), and
that any global role outside the policy stays out however the vault is configured. Those two are
meant to arrive **when actually relevant** — through an explicit reference or future M2N retrieval,
as typed candidates through the existing seam, budgeted and provenanced like everything else.
**No keyword heuristic guesses at that relevance**, deliberately. The grant remains the gate:
revoking it between two builds takes effect on the second one, because it is re-read every time.

**`source_ref` is a semantic address and never a location.** `project:cofferdam:plan#m2j`,
`global:communication_style`, `workspace:cofferdam:working_context`. A separator, a home marker, a
parent segment or an unknown scheme is refused **at construction**, so a reference that could leak
a path never exists to be filtered later — and section identities are restricted to `[a-z0-9-]`,
which is what stops a heading like `## ../../etc/passwd` putting a path-shaped string into
provenance. `observed_at` is when Cofferdam read the source, never when the document was written.

**Nothing is logged.** Not "logged carefully" — the package emits no log records, the same posture
the mind and workspace packages take. `pack.summary()` exists for a caller that wants a journal
line and carries structural facts only: counts, kinds, references, byte totals, truncation count
and omission reasons. Tests assert that building a pack emits no record at all and that the
summary contains none of the sentinel personal strings the fixtures plant.

**A read is a read.** Tests assert no canonical Markdown is written, no memory proposal is created,
no Working Context revision advances, and **no file appears anywhere under the home** during a
build. Unmapped project documents, unmapped vault notes, nested vault directories and `.obsidian/`
are not filtered but **unreachable**: the builder has no way to name a file.

**One packaging defect was found by the tests rather than by reading the code** — the new package
was missing from `[tool.setuptools] packages`, which `tests/test_packaging.py` caught and which
would otherwise have shipped a wheel without it.

**No routes.** PR3 adds no HTTP route to the workstation or the Actions bridge and no PWA surface.
Route surfaces are compared against `f279fc2` and are byte-for-byte identical, and the OpenAPI
document is unchanged.

**Not in this PR:** `CloudContextProjection` and any egress (still D-2026-08-11-5's separate
object); the PWA workspace panel and `get_project_context` (PR4); `syncWorkspace` (M2M); evidence and
evaluation (M2K); the planner, any model runtime, tokenizer or prompt construction (M2L); the
dashboard (M2M); embeddings, vectors, links and backlinks traversal (M2N). No persistence, no
cache, no new configuration file, no browser work, no new adapter, and no change to the mind
proposal/apply path, `delegated_adapter` or Task Core.


### M2J PR2 — mind access, the host-owned grant, and the memory-proposal queue

**Merged as `1c45b26` (#38) and deployed to slot B**, with the `cross_project` role following as
PR2.1, **merged as `f279fc2` (#40)**. Written on `feat/m2j-mind-proposals`, from the merged
`ae5c025`, while it was still a branch — where this entry says "not merged" or "not deployed" it
is describing the moment it was written. Documented in [`docs/MIND.md`](docs/MIND.md).

**Cofferdam can now read memory by role, and can be *allowed* to change it — never on its own.**
Two authorities, kept apart. **Project mind** is the project's own repository, reached through the
active workspace's project and addressed by a code-owned role rather than a filename; Cofferdam's
own `STATUS.md`, `ROADMAP.md`, `DECISIONS.md` and `DESIGN.md` are mapped, and **no `PLAN.md` was
added** to satisfy a naming convention. **Global mind** is a dedicated Obsidian-compatible vault
outside `$COFFERDAM_HOME`, readable only under an explicit host-owned grant in
`config/mind-grant.json` — a file that does not exist until somebody writes it, **and that grants
nothing until it says `"enabled": true`** (D-2026-08-12-2). That is deliberately stricter than the
project and workspace convention, where `enabled` defaults to on: those files say where work
happens on this machine, while this one decides whether personal cross-project memory is readable
at all, so the activating act is made explicit rather than incidental. A grant with `enabled`
omitted, set to `false`, or set to `1`/`"true"` grants nothing and reports why — the check is a
type check rather than truthiness, because `1` and `"true"` are exactly what somebody writes
meaning yes. It is re-read on every resolution, so revocation reaches a pending proposal:
acceptance refuses and writes nothing, and the proposal stays pending rather than being decided.
Nothing scans a home directory, nothing offers a vault it found, and Cofferdam never chooses where
yours lives.

**A request names a role; the host decides which file that is.** Nine role words across two
disjoint vocabularies, matched *before* anything is resolved — so a role sent as
`../../etc/passwd` is not sanitised, it simply is not a role, and the refusal happens before any
filesystem call. There is no field, path segment or query parameter anywhere for a path, root,
working directory, filename, URI or command. Resolution re-verifies the root, walks every
component below it with `lstat`, refuses a link anywhere, and confirms the resolved path is where
it should have landed — `realpath` alone would follow a link out of the vault and report success.

**Writing is proposal → explicit acceptance → hash-bound atomic apply.** Creating a proposal
writes **zero Markdown** and records both the target's current content hash and an opaque
fingerprint of the host authority that resolved it. Acceptance re-resolves the role from
configuration re-read at that moment and refuses if *either* moved: a different document behind
the same role is `mind_target_authority_changed`, drifted bytes are `mind_proposal_stale`, and
both write nothing. A content hash alone could not tell those apart — remap a role to a
byte-identical file and it still matches — which is why the binding is recorded as well
(D-2026-08-12-3). No three-way merge, no silent refresh: a new proposal is a new review.

**The apply protocol is crash-truthful.** `pending → applying → applied`: the claim is committed
before the filesystem is touched and says only that somebody started, so the store can never
durably say `applied` while the document still holds the pre-apply bytes. The claim is a durable
compare-and-set, so two acceptances can never both write. At start-up each outstanding claim is
classified from the document's own hash — landed, did not land, or conflicted — and **recovery
never performs a write**: an apply that did not land becomes `interrupted` and waits for a person.

**Containment is descriptor-relative.** A descriptor is opened on the verified root, every
component below it is opened relative to the one above with `O_NOFOLLOW`, and the parent
descriptor is held for the whole operation — so the hash that authorizes a write and the write
itself concern one file, and an intermediate directory swapped in afterwards redirects nothing.
The temporary file and the rename are both relative to that descriptor. There is no pathname
fallback: a platform without the primitives refuses rather than degrading. **Exactly one file
changes.** A failed replace leaves the document byte-identical and the proposal decidable.

**Deletion is absent rather than refused.** The operation vocabulary has one word,
`replace_document`, and no function in the package removes or creates a path. An *empty* proposed
document is refused too, because a replace with nothing in it is a deletion wearing a mutation's
clothes. PR2 modifies existing approved documents only, and **creating an approved-but-absent
memory document is recorded as out of scope pending its own authority decision** (D-2026-08-12-2)
rather than left as an implementation gap: a grant that may create files is a grant over a
*directory*, not over the documents an operator named.

**Acceptance is the device token and nothing else.** All seven `/api/mind*` routes use
`require_token`, which has never heard of the bridge credential — so a bridge request is a 401
because nothing there can recognise it, not because a check refuses it. D-2026-08-11-4 requires
that the planner and the bridge have *no acceptance route at all*, and a test builds the real
bridge application and asserts no route of it mentions mind, memory, a proposal, a vault or a
document. **The Actions bridge is not modified by this PR.**

**`documents` on a workspace is PR1's own rule being kept, not broken.** PR1 left the role map out
because nothing read it; PR2 is the reader, so it arrives now — and it is not a second path
authority, because the directory still comes from the project. A role mapped twice is refused
rather than resolved by load order: the file is parsed with a hook that rejects duplicate keys,
since `json.loads` silently keeps the last one and that would make file order the authority over
which document a role resolves to.

**Nothing leaves the host.** This PR adds no provider client, no bridge Action, no worker context
and no projection. `CloudContextProjection` (D-2026-08-11-5) still does not exist, which is the
honest state and the reason a caller cannot accidentally be in a different one.

**Backward compatible by absence.** No `documents` map means no project mind; no
`config/mind-grant.json` means no global mind; and **no database is created by a read** — listing
proposals on a host that has never proposed anything answers from nothing and leaves no file.
Deleting `state/mind/` forgets the pending proposals and touches no Markdown.

**Not in this PR:** the Context Builder and `LocalContextPack` (PR3); `CloudContextProjection`
and the egress policy (PR3.5); the PWA panel and `get_project_context` (PR4); `syncWorkspace` (M2M); evidence and evaluation (M2K); the planner and any model
runtime (M2L); the dashboard (M2M). No vectors, no retrieval, no summarization, no token budgets,
no browser work, no new adapter, and no change to `delegated_adapter` or Task Core.

### M2J PR1 — workspaces and durable Working Context

**Merged as `ae5c025` (#36) and deployed to slot B.** Written on
`feat/m2j-workspace-working-context`, from the merged `ebe1a78`, while it was still a branch —
where this entry says "not merged" it is describing the moment it was written. Documented in
[`docs/WORKSPACES.md`](docs/WORKSPACES.md).

**Cofferdam now owns "what are we working on", and it survives a restart.** A workspace is
host-owned configuration in `config/workspaces.json` — a stable id, a label, and the id of a
project that already exists — beside `task-projects.json` and validated the same way. Working
Context is Cofferdam's own durable state in its own SQLite database under `state/workspace/`:
the active workspace, each workspace's objective and its history, and four bounded continuity
references. Six private routes under `/api/workspace*`, all on the device token.

**Context is keyed by workspace rather than stored once**, and that is the design decision worth
recording. One global objective would mean switching workspace left the previous objective on
screen describing something nobody is doing, and switching back lost it. Per-workspace rows make
the switch a pointer move and make cross-workspace confusion structurally impossible instead of a
rule to remember.

**Nothing derived is persisted.** Task state, bucket and terminality are asked of Task Core on
every read; the delegated worker is resolved through the workspace's project on every read. A
stored `task_state` would be correct for seconds and then wrong with nothing announcing it, and a
stored worker would be a second adapter authority holding a stale copy — strictly worse than the
ordering bug PR #34 fixed. Both are asserted by changing the underlying fact behind the store and
re-reading.

**A task reference is a reference.** `live`, `terminal` or `missing`, resolved live. A terminal
task keeps its reference, because it finished and that is the fact somebody came back to read; a
task Task Core no longer has is reported `missing` with its id rather than blanked. Pointing at a
task in another project is refused.

**The workspace cannot become a second authority.** `root`, `path`, `adapters`,
`delegated_adapter`, `model`, `provider` and the execution words are refused *by name* in the
config schema, with a message saying where that decision actually lives, and refused again by
allowlist at every route. There is no create route: workspaces are edited on the host, and nothing
auto-registers one for an existing project.

**The Actions bridge reaches none of it.** These routes use `require_token`, which has never heard
of the bridge credential, so a bridge request is a 401 because nothing can recognise it rather than
because a check refuses it. A test enables the bridge caller, presents the real credential to all
six routes, and uses a task route as the control. `syncWorkspace` is M2M's (D-2026-08-13-4) and no
external surface reads the workspace today.

**Backward compatible by absence.** No `workspaces.json` means no workspaces, every existing task,
Custom GPT and Claude flow is untouched, and **no database is created** — a read never opens one,
because the PWA polls and an ordinary connect would manufacture a state directory out of somebody
looking at a screen.

**Two defects were found by writing the tests rather than by reading the code**: a failed
schema-version check abandoned an open SQLite connection holding a lock on a database the process
had just decided not to touch, and a plain read created the database on an unconfigured host,
which contradicted the backward-compatibility claim this PR makes. Both are fixed and both have
regression tests.

**Not in this PR:** the mind, the vault and memory proposals (PR2); the Context Builder and
`LocalContextPack` (PR3); `CloudContextProjection` and the egress policy (PR3.5); the PWA
workspace panel and `get_project_context` (PR4); `syncWorkspace` (M2M); evidence and evaluation (M2K); the planner and any model runtime (M2L); the dashboard
(M2M). No document-role or profile fields were added, because nothing reads them yet. *(PR2 added
the `documents` role map once it had a reader; the profile fields are still absent.)*

## M2I.5 records (all merged; written while each was on its branch)

As with the milestone records above, these were written while their branches were open and are
kept as those PRs' records. **All three are merged on `main` now** — PR1 `e078251`, PR2 `de15bd73`,
PR3 `2386a54` (#34) — so where an entry below says "on a branch" it is describing the moment it was
written. Production runs `2386a54` in slot A with both Claude adapters registered.

### M2I.5 PR3 — Gate B, production Agent SDK delegation

**Merged as `2386a54` (#34).** Written on `feat/m2i5-agent-sdk-gate-b`, from the merged `de15bd73`.
**Implemented, deployed and validated live through the real private Custom GPT.** Sanitized
evidence:
[`docs/checklists/m2i5-gate-b-validation.md`](docs/checklists/m2i5-gate-b-validation.md).

**The precondition, and it was worse than it read.** The Actions bridge has no `adapter_id` field —
a model provider choosing which agent runs on somebody's workstation is the shape M2I.5 exists to
prevent — so it took *the first adapter the project listed*. That was defensible only while every
delegated project permitted exactly one adapter, which is precisely the condition Gate B ends. It
was also not the operator's ordering: `TaskProject` sorts the adapter list at load, so "first" meant
**alphabetically first**, and `claude-agent-sdk` would have silently beaten `claude-code` because
`a` sorts before `c`. Nobody would have chosen that rule and nobody would have seen it apply.

A project may now name one adapter in `delegated_adapter`, resolved on the host by
`TaskProject.delegation` into one of four published words. Ordering is authority nowhere; a project
permitting one adapter still resolves implicitly, so no existing registry had to be rewritten;
several permitted with none delegated fails closed; and a delegation is a *selection among things
already permitted*, never a grant — naming an adapter the project does not permit, or that this
build never registered, resolves to nothing. There is no fallback anywhere in the path, including
for a payload with no `delegated_adapter` at all, which is what an older daemon than the bridge
would send.

**No published contract changed.** `Project` is `additionalProperties: false` in the Custom GPT
schema and `project_not_eligible` was already in the declared error enum, so the consequence
surfaces through fields that already exist. The nine operations, the OpenAPI document and the GPT
instructions are untouched, and the real GPT needed no edit.

**Deployed:** the candidate slot with `workstation`, `actions-bridge` and `agent-sdk` extras;
`claude-agent-sdk 0.2.134`, the exact version the adapter records as verified. Its wheel bundles its
own CLI (2.1.226) which Cofferdam does not use — the adapter pins the host's own `claude` (2.1.221),
the binary whose sign-in the workstation manages. The adapter is enabled by one removable drop-in
carrying a single environment variable and touching no `ExecStart`, so removing it revokes exactly
one capability. **The Claude Code adapter stays enabled and `claude-sandbox` still resolves to it**,
implicitly, having gained no registry field at all.

**Validated live:** one task in a new disposable `agent-sdk-sandbox`, every mutation driven by a
person through the real private Custom GPT on the native iPhone app. One real `AskUserQuestion`, two
options, the displayed choice mapped to the **Cofferdam-minted `option_id`** with the free-text
field `null`, answer source `future_gpt_bridge`, the **same provider session** across the
continuation and across one normal follow-up, then `finishTask` releasing the session. Two turns,
one clarification, one accepted answer, results `Selected: Beacon` and `Follow-up received.`, and
the sandbox byte-identical afterwards — same tree, same three blob hashes, zero untracked files.
All four consequential Actions prompted on mobile rather than mutating silently.

**Still unsupported, deliberately:** free text, multiple choice, several questions at once, and
"Other + custom text". The live run exercised one single-choice question and establishes nothing
about the others; nothing was widened to fit, and `SCHEMA_VERIFIED` was not edited. Tool approvals
are still never bridged — `can_use_tool` denies, and there is no approval route the bridge can
authenticate to. The project root is still a configuration boundary the CLI is asked to respect,
**not a kernel sandbox**.

### M2I.5 PR2 — Gate A, the connected private Custom GPT

On `feat/m2i5-actions-exposure`, from the merged `e078251`. **Exposed, deployed, connected and
validated.** See [`docs/ACTIONS_EXPOSURE.md`](docs/ACTIONS_EXPOSURE.md) for the deployment and its
rollback, and `validation/` on the host for the sanitized Gate A evidence.

Production moved to the inactive slot at merged main; the previous slot is retained untouched as
the rollback target. Two new user services run beside the daemon: the Actions bridge on loopback,
and one cloudflared connector. The project registry is unchanged, the Claude Code adapter is still
the only enabled provider adapter, and no Remote Control host, Tailscale Serve or Funnel exists.

Three defects in PR1 surfaced only by performing Gate A, and each is fixed here: `httpx` was
declared as a test-only dependency although the bridge imports it at module scope; httpx's own
per-request log line carried the canonical task id that `observe.py` exists to keep out of the
journal; and the operator instruction block was ~11,200 characters against an 8,000-character
Instructions box, so the file everyone was told to paste could not be pasted. A fourth was found in
the live deployment: the bridge's idempotency table was created at the process umask while the
daemon's task store beside it is 0700.

A fifth was found by the real GPT editor rather than by any validator: a parameter declared with
`$ref` is read as nameless and the **whole operation is skipped**, which silently removed five of
the nine Actions while the import reported success.

### M2I.5 PR1 — the private Custom GPT Actions bridge foundation

Merged as `e078251`. **Local only at the time: nothing exposed, nothing deployed, no Custom GPT
configured, no production file changed.**

A separate narrow process — `python -m cofferdam.actions_bridge` — that publishes eight bounded
Actions under `/v1` and reaches Cofferdam through ten fixed, allowlisted internal calls. It is not
the PWA, not the main API, not a proxy and not a mirror of the task routes: there is no route in it
that forwards a caller's path, method, header or query string, and the only way out of the process
is ten named methods with no URL parameter between them.

**Three credentials, three jobs.** The device token keeps the whole private API. A new
**bridge-internal token** — 0600, generated only under `--enable-actions-bridge-caller`, off by
default — is recognised by the daemon on **ten task routes and nothing else**. A separate
**external key** is what the Custom GPT holds and is the only credential a remote caller ever
presents. The daemon's other routes still use `require_token`, which has never heard of the bridge
credential, so a bridge request to `/api/remote-control/...` is a 401 rather than a check somebody
could later relax.

**The scoped credential exists for provenance as much as for access.** M2I PR2 reserved the words
`chatgpt_app` and `future_gpt_bridge` and deliberately left them out of the accepted sets, writing
that "a reserved word is not an enabled surface". A surface now exists, so they are in — and a
bridge-created task is recorded under the bridge's own name rather than as though somebody had used
their phone. Two tests that asserted the reservation were replaced by tests that assert the
labelling, which is the property that was always the point.

**The prompt is withheld at the daemon, not filtered at the bridge.** `GET /api/tasks/{id}` returns
no `prompt` key at all to the bridge caller: the bridge composed that text from somebody's ChatGPT
conversation, and handing it back would let a model provider re-read it on a schedule.

**One question shape, and no fabrication.** `submitChoiceAnswer` takes exactly one `option_id` and
**has no text field at all** — not optional, not validated-and-refused. Free text, multiple choice,
an unknown mode or options that lost their ids all come back as `clarification_supported: false`
with the real question text intact and nothing invented in its place. "Other plus custom text" is
reported as unsupported rather than approximated.

**Tool approvals are excluded by there being nothing to expose.** The private API has no approval
route, so the bridge has none either. A waiting approval is reported as
`local_action_required` and pointed at the workstation.

**Artifacts are unavailable, and the reason is recorded rather than worked around.** Cofferdam has
no task-owned artifact model — `EvidenceReference` is an unverified adapter claim with a free-form
identifier, explicitly documented as never dereferenced. `artifacts_supported` is `false` with a
reason word, and [`docs/ACTIONS_BRIDGE.md`](docs/ACTIONS_BRIDGE.md) states the exact five-step Task
Core PR that would have to come first. No path parameter was added.

**Idempotency without a second task database.** Every mutation needs a `client_request_id`. A small
bridge-owned SQLite table maps `(operation, scope, request_id) → (digest, task_id)` and stores **no
request body**; on replay the bridge re-reads the current state from Task Core rather than
returning a stored response. `createTask` and `sendFollowup` pass the same key upstream, where Task
Core has its own idempotency — two independent guards on one retry.

**232 bridge tests plus a local end-to-end suite** that runs a real bridge against a real daemon
over real loopback HTTP with the validation adapter and a sanitized clarification fixture: no
model call, no network, no production contact. That suite found a real bug — the idempotency
store's replay path returned without ending its transaction, which only fails on the *second*
replayed request.

**What this does not claim.** Nothing about the real Custom GPT. Local HTTP over loopback says
nothing about ChatGPT's request shaping, its confirmation prompts, its retries or its 45-second
budget. `servers[0].url` in the schema is a placeholder pointing at `.example.invalid`. Production
is untouched: same slot, same drop-in, same registry, same Claude Code adapter, no restart.

**The next two decisions are separate.** Gate A is external HTTPS exposure and a real Custom GPT
preview. Gate B is production Agent SDK enablement. Either can be approved without the other.

### M2I PR4 — the phone surface, helper cleanup and startup reconciliation

**Merged as `1a7d66b` (#31), closing M2I.** The record below was written while it was on
`feat/m2i-production-readiness` and is kept as that PR's record. PR1 merged as #28, PR2 as #29,
PR3 as #30. **Still not deployed and still off by default:** production runs the Claude Code
adapter, and enabling the Agent SDK there is Gate B of M2I.5.

**The headline feature of M2I could not be used from a phone, and that was the finding.** PR2
shipped the clarification routes and nothing called them: the PWA rendered a generic "Your answer"
box for any `waiting_for_user` and posted it to `/followups`, a route the server refuses outright
while a question is open. The two routes had been apart on the wire since PR2 and together on the
screen ever since. The panel now reads `/clarifications`, renders the normalized question with its
options, and answers through the dedicated route with a body of exactly `answer` and `option_ids`.
While a question is open there is no follow-up box. **There is still no approval control and no
route for one**, and the panel says on screen that answering is information, not permission.

**Drafts survive the page, not just the poll.** The old rule was "the panel stores nothing", which
was right while it had nothing worth keeping and wrong once the thing not kept was somebody's
half-written instruction to an agent — iOS discards a backgrounded tab whenever it likes. Drafts
are now keyed `cofferdam.taskdraft.<operation>.<task_id>`, so a clarification answer can never
reappear as a follow-up and one task's words never land in another's box. One writer, one storage
mechanism, every access guarded the way `app.js` learned from a real device. Drafts go on a
terminal state and **all of them go on sign-out**. No token, no provider session id, no provider
payload is stored, and there is no second `setItem` through which one could arrive.

**A retry is recognisable as one.** One module-level slot held the request key for every write and
was cleared on *any* response, including a refusal — which is exactly when somebody presses the
button again, so the retry arrived at the server as a second, unrelated message. Keys are now
scoped by operation and task, retained across a refusal, and regenerated only when the words
change. Unlocking the phone triggers **one** foreground read rather than a new timer, and polling
stays stopped while hidden.

**Two things Cofferdam owned could outlive their owner.** `ClaudeAgentSdkAdapter.shutdown` was
implemented in PR1 and never called, so a daemon stopped with a live task left its helper to work
that out for itself; the registry now asks every adapter from the daemon's lifespan. And
termination reached only the helper, while the SDK's own CLI runs inside the helper's process
group — a terminated helper could orphan that CLI with a live subscription session. The stop now
signals the **group**, under the Claude Code adapter's ownership rule: pid, `/proc` start time and
group id recorded at launch and **all three** re-verified before every signal. Nothing enumerates
processes or matches a name. `HostSession.note_lost` also had no caller, so a helper that died and
one that merely had nothing to say produced the same history line; they no longer do.

**The structural guard got narrower about the right thing.** `hostclient.py` used to be barred from
naming a pid or a group at all. That sounds stricter and prevented cleanup rather than leakage.
What stays forbidden is everything that makes a stop *broad* — `os.kill` on a bare pid, `psutil`,
`pkill`, `killall`, `pidof`, any process-name match — and a test asserts the identity check appears
before the signal.

**Restart reconciliation now covers the states M2I added.** `ready_for_followup` with no live
helper and a pending question are the two where being wrong is least visible, because both read as
"waiting for you" with nothing on the other end. Both become `interrupted`; the question closes as
superseded in the same write; earlier completed turns stay retrievable; terminal tasks are not
touched; and running it four times settles one task once.

**The profile has a name.** `cofferdam-project-edit-v1`, published as data and carried in the
capability description, so it can be read off a running build rather than a document. Values are
unchanged. The filesystem claim is deliberately the weaker true one: `cwd` with empty `add_dirs` is
a configuration boundary the CLI is asked to respect, **not a kernel sandbox**, and this build does
not claim otherwise. What it does claim is that there is no shell, so there is no general execution
primitive to escape with.

**Every task-content route is `no-store`** — detail, events, questions, result — and the adapter
list deliberately is not, so the header means something.

**Tests.** Focused additions across the SDK suite, the PWA suite, the browser harness and the
clarification API. Process ownership is proved against **real short-lived processes**, because it
is the one property a double cannot prove; everything else runs against doubles. No test calls
Anthropic, uses the network, consumes model usage, inspects a transcript, modifies the live
registry, starts Remote Control or touches production.

**Validated on a real phone, over three supervised runs, and the first two failed.** Each ran on a
temporary tailnet-only daemon with its own `COFFERDAM_HOME`, one-project registry and token, from a
**disposable worktree** so the PR branch stayed byte-clean, with the session narrowed below the
shipped profile and restored by deleting the worktree.

Run 1 exercised the whole workflow and passed sixteen of seventeen checks — structured question
answered through the clarification route, both drafts surviving a locked screen, neither
auto-submitting, immediate foreground refresh, one provider session, nothing leaked to the phone.
It failed one: **an accepted follow-up did not clear its draft**, and that was not cosmetic. The
draft is deliberately not in the markup, so clearing the store left the live textarea holding the
accepted text and the next render's `captureDraft` wrote it back; with the request id released
alongside, the next tap resent the same words under a new key. One intended message produced **three
provider turns**.

Run 2 rechecked the fix and produced three turns again — from a daemon serving the corrected file.
The cause was isolated with **no provider call**: the real `tasks.js` was run in a real browser
against a stubbed API at both commits. Pre-fix the box kept its text and a second tap posted twice;
fixed, it emptied and posted once. The DOM fix was sound; **the phone was running the old file.**
Assets carried `ETag` and `Last-Modified` but **no `Cache-Control` at all**, and a response silent
about its freshness may be given a heuristic lifetime — iOS Safari does exactly that. Every static
asset now says `Cache-Control: no-cache`: keep it, but ask before using it.

Run 3, on `935d455` with a visible build marker, **passed**: one task, two turns (`Ready.` then
`Atlas.`), one accepted follow-up under one request id, one provider session, no tool or
clarification event, the follow-up field empty after acceptance and still empty after a reload, and
an empty Send producing only the local refusal.

The middle run is the one worth remembering: a fix that is correct in the repository, correct under
test and correct in a browser is still not a fix a device has received.

**No production change.** No unit, drop-in, installer or registry file was edited; the SDK is not
installed in the production slot and the adapter is not enabled there. The **Claude Code adapter
remains the production transport and the fallback**, unchanged.

### M2I PR3 — same-session follow-up and the `get_result` boundary

On `feat/m2i-followup-results`. **Not deployed, off by default, and no live SDK call was made
from this repository.** M2I PR1 merged as #28 and PR2 as #29; the record for PR1 below describes
the foundation both of them built on.

**A task can now be several turns.** A turn-ending result no longer ends the session: the helper
keeps the same `ClaudeSDKClient`, and another `query()` on it continues the same provider
conversation. Three facts read from the published 0.2.134 source make that sound rather than
hopeful — a result frame ends one turn and not the run, `receive_messages()` keeps yielding past
it, and `connect()` with no prompt never closes stdin, because the SDK only spawns the
stdin-closing input stream for an `AsyncIterable` prompt. Cofferdam already called `connect()`
that way for the permission-callback reason.

**Turns are durable, and one never overwrites another.** Schema version 3 adds `task_turns`,
additive and backward-compatible from version 2, writing no rows on upgrade. `turn_number` is
allocated `MAX+1` inside the transaction that moves the task, with the primary key as the
backstop, and a completed turn is never written again — the update is guarded on `completed_at IS
NULL`. That guard is what stops a second turn, a duplicate provider event or a late result after
a cancellation from rewriting an answer somebody has already read. `tasks.final_result` still
moves on, because it is written with `COALESCE`, which is precisely why the table exists.

**`get_result` has one stated meaning.** `GET /api/tasks/{id}/result` returns the latest
*completed* turn's result; `task_terminal` distinguishes a task that may still produce more from
one that is finished, and the payload carries `result_meaning` in words. A task whose first turn
answered and was then cancelled returns that answer with `outcome: cancelled` — both facts are
true and the response says both. A live task with nothing yet is `task_result_not_ready` (409, not
404: the task exists). It is a read and only a read.

**The follow-up contract is narrow and each refusal is its own sentence.** Allowed from
`ready_for_followup` with a live session and no question open. A pending clarification refuses a
follow-up outright rather than superseding the question — a person typing a new instruction while
the agent waits to be told something specific has not answered it. Whether a session is still
there is asked of the adapter, fresh, because the state name is Cofferdam's memory of an
observation rather than the observation.

**Restart stays truthful.** The live client is in memory inside the helper, so after a restart the
adapter's session dictionary is empty and every task answers "not continuable" as a consequence of
the world rather than a flag. The task becomes `interrupted`, the turn in flight closes as
`interrupted`, and **every earlier completed turn is untouched** — an interrupted task still
returns the result it produced. **Cross-process `resume` is not used and not evidenced.**

**Three concepts, three code paths.** A clarification answer, a follow-up and a local tool
approval remain impossible to confuse: separate routes, separate helper commands, separate session
methods, and a body shaped like one refused by the others. There is still no tool-approval route,
table or field anywhere.

**Tests.** 118 new focused tests, including `SdkSessionTurnTests`, which drives the **real**
`SdkSession` — thread, loop, receive loop, between-turn park, session-identity check — against a
scripted async client, so the multi-turn code under test is the code that ships. Full suite green
in three configurations: stdlib-only, workstation extras without the SDK, and extras with
`claude-agent-sdk 0.2.134` installed.

**One supervised live spike, and it found something.** One disposable task on `claude-sandbox`
against a non-production loopback daemon, with the session tightened below the shipped profile for
the run (`tools: []`, USD 0.50 budget, `PROFILE_MAX_TURNS` unchanged, reverted afterwards). Turn 1
answered `Blue.`; the follow-up was accepted from `ready_for_followup`; **both turns reported the
same `provider_session_id`** and were served by one helper process and one SDK client; turn 2
answered *"I named the colour blue."*, which it could only do from turn 1's context. Turn 1
remained retrievable, an idempotent retry created no second turn, no tool, approval or
clarification event occurred, the sandbox was byte-for-byte unchanged, and the database contained
no raw payload, reasoning, transcript, environment value or credential.

The defect it found: the adapter emitted its own "your follow-up was delivered" event *and* the
session emitted one when the turn actually began — two near-identical history lines, the first
carrying an already-stale turn number. The adapter's was removed.

**No production change.** No unit, drop-in, installer or registry file was edited; the SDK is not
installed in the production slot and the adapter is not enabled there. Production's PID, start
time, drop-in hash and registry hash were recorded before the spike and verified unchanged after.

**Next:** the remaining M2I parity gap before the CLI adapter can be retired — see
[`ROADMAP.md`](ROADMAP.md).

### M2I PR1 — the Claude Agent SDK foundation and structured session events

Merged as #28. The first PR of Lane B's M2I: the official
**Claude Agent SDK** as a second delegated-task transport, and the provider-neutral event
vocabulary that makes a structured question channel possible. **Not deployed, off by default, and
no live SDK call was made from this repository.**

**Verified before anything was written.** The SDK contract was read from the published
`claude-agent-sdk` distribution rather than recalled: distribution `claude-agent-sdk`, import
`claude_agent_sdk`, version `0.2.134`, MIT, `Requires-Python >=3.10`; `ClaudeSDKClient` with
`connect`/`query`/`receive_messages`/`interrupt`/`disconnect`; the `ClaudeAgentOptions` dataclass;
the typed message and content-block families; and `can_use_tool` returning
`PermissionResultAllow`/`PermissionResultDeny`. Three findings changed the design: the wheel is
about 91 MB and bundles its own CLI (Cofferdam pins the host's instead), `can_use_tool` refuses a
string prompt passed to `connect`, and `ClaudeAgentOptions.env` layers over the daemon's
environment rather than replacing it — the one place this adapter is weaker than the CLI one, and
it is stated rather than papered over.

**A dependency boundary that means something.** `agent-sdk` is its own extra with a
`python_version >= '3.10'` marker and a `<0.3` bound. Exactly one function imports the SDK, from
inside adapter methods; importing Cofferdam, starting the daemon and running the entire suite
never import it, and a source scan enforces that. A missing SDK, an old interpreter and an
incompatible version each get a different, precise sentence. The whole event model, tool policy
and adapter behaviour are tested without the dependency, so the stdlib-only CI job keeps its
meaning.

**Clarification and tool approval are separated at the storage layer.** Two dataclasses with
disjoint required fields and disjoint serialized shapes; each refuses a payload carrying the
other's fields even when the discriminator looks right; they map to different waiting reasons and
project to visibly different history entries. A clarification may one day be answered from a phone
or a Custom GPT; **a tool approval never will**. In this foundation the SDK's permission callback
is a code-owned handler that denies and records, reading the tool's name and none of its input.

**What it deliberately does not do.** Neither request moves the task into `waiting_for_user`: an
approval is not a wait, because Cofferdam denied it and the agent carries on, and a clarification
has no answer channel yet — parking a task there with nothing able to answer it would strand it,
since `waiting_for_user → completed` is absent from the graph on purpose. There is no follow-up
(the seam is preserved and the capability is not claimed), no `get_result` route, and no
production change of any kind.

**Cancellation and results.** Task Core stays the authority; cancellation reaches the SDK's own
`interrupt()` on that task's client, with no signal, pid or name matching anywhere. A result that
already arrived beats a later cancel; a result arriving after a cancellation is dropped by the
event log's finality rule. A terminal event produces a provider-neutral result — bounded output or
a failure category with a Cofferdam-worded summary, plus provider and session provenance — with no
stack and no raw payload.

**Storage.** The normalized events project onto Task Core's existing generic event storage. **No
schema migration, no second table, no second database of delegated tasks.**

**The Claude Code adapter is unchanged and remains the fallback.** Both adapters can be
registered; they have different ids; enabling one never disables the other; a duplicate adapter id
is now a start-up failure rather than a silent overwrite. The retirement rule in
[`ROADMAP.md`](ROADMAP.md) is unchanged: the CLI adapter goes only after verified parity with the
behaviours PR #21 validated live.

**No production validation is claimed.** No Anthropic call, no model usage, no login, no network,
no subprocess, no transcript, no live registry change, no service restart. Documented in
[`docs/CLAUDE_AGENT_SDK_ADAPTER.md`](docs/CLAUDE_AGENT_SDK_ADAPTER.md).

**Followed by:** M2I PR2 (#29) — the structured clarification-question round trip, answer
provenance, and strict separation from local tool approvals — and M2I PR3, above.

## Recently merged milestone records

### M2H PR4 — unattended recovery validated, and the Remote Control milestone closed

Merged as PR #27 (`0818d25`). **M2H is complete.** PR1 (#23), PR2 (#24), PR2.5 (#25) and
PR3 (#26) are merged; this is the validation that was always the point of them.

**Cold reboot passed.** A real reboot, then the phone, then — much later — the desktop. Cofferdam
was reachable over the tailnet **50 seconds after power-on and 6h48m before anyone logged in**,
the previously saved device token was accepted with no re-entry, and from the iPhone the full
flow worked: `claude-sandbox` showed **Stopped**, Start brought it to **Running** without a manual
reload, `awaiting_consent` stayed false, **Open Remote Control** opened the correct native Claude
environment, and Stop returned it to **Stopped** with Open no longer offered. Server side agrees:
the host started at 00:58:17, captured its link 2 seconds later, and exited on request at 00:58:58
with status 0, leaving an empty runtime-state directory. The full evidence table is in the closed
M1 gate above.

**What the audit found on the way, which mattered more than the boot path.** Every milestone since
M2A had installed a `cofferdam-workstation.service.d/*-validation.conf` repointing `ExecStart` at
its own feature worktree, and none were removed. Twelve had accumulated; systemd applies drop-ins
in lexical order, so production had been running **M2G-era code out of a feature worktree** since
PR #21, with a validation-only task adapter enabled. No test caught it, because every test
asserted the *shipped* unit — which was always correct — and the drift lived only in the installed
drop-in directory. Production now runs `slots/a` at the merged commit, and
`tests/test_deployment_drift.py` plus the read-only `deploy/preflight.sh` guard both halves.

**Boot behaviour, recorded because it is load-bearing:** user linger is enabled and is what makes
the user manager start at boot — without it a rebooted machine has no Cofferdam until somebody
signs in. `tailscaled` is enabled at boot but is not something a *user* unit can order against, so
the daemon waits for its own bind address in-process for up to 120s and never falls back to
another interface; this boot it waited 6 seconds and bound cleanly.

**Remote Control hosts do not auto-start after a reboot, by design.** The installed
`cofferdam-rc@.service` template has no `[Install]` section (`UnitFileState=static`), so no
instance can be enabled and none came back after the reboot. The property this milestone claims is
only that a person can *start* one from the phone once Cofferdam has recovered.

**The boundary that has not moved:** stopping the local host removes the link from Cofferdam and
**does not revoke an Anthropic environment URL already shared elsewhere** — the URL is scoped to
the environment, not to a launch, and Cofferdam has no account-level revocation mechanism.
Transcript reading and prompt injection remain out of scope permanently under D-2026-08-08-3; no
conversation content is read or stored anywhere in this lane.

**Remaining limitations:** automatic login is not enabled on this host, and a Remote Control host
must still be started deliberately after every reboot. Graphical-session capability reporting
before and after login is no longer among them — it was observed across a real logout/login cycle
on 2026-08-11 (see the [M2B record](#m2b--runtime-inventory)).

## Planned (active roadmap — see [`ROADMAP.md`](ROADMAP.md))

**Replanned 2026-08-11 and recorded as D-2026-08-11-1 … -12.** **M2J is done** (see *M2J closeout*
above); nothing else below is implemented, and no code was written for any of it. Queued, in order:

- **M2K — evidence and evaluation foundation. Next.** Model-free. Per-turn evidence bundles that
  keep worker *claims* and Cofferdam *observations* structurally apart, deterministic criteria
  checks before any model, risk levels derived from code and policy rather than model
  self-selection, and the first machine-observed failure reason codes.

  **The handoff, in the terms M2K is bound to.** A worker's final message is **only a claim**;
  deterministic, machine-observed evidence comes first, and a model may later **downgrade**
  confidence but must **never upgrade** failed or unverified evidence (D-2026-08-11-6).
  Check-command authority is **code-owned or host-owned** — the planner, the worker, a remote
  caller and a task prompt never supply executable text (D-2026-08-11-7). Task Core is currently at
  **schema v3**, and M2K's persistence is an additive **v4** on the same additive-only discipline.
  **Start with the five-step artifact/change-claims Task Core PR that D-2026-08-09-3 already
  specifies** — it is the foundation the rest of the milestone reads from, and it is deterministic
  and model-free.
- **M2L — Local Planner MVP.** One local model, one role, advisory throughout: Turkish-first
  conversation, worker-prompt and follow-up drafting, evidence interpretation, next-step
  recommendations, honest refusal. Every consequential proposal is explicitly confirmed; there is
  no autonomous planner → worker continuation in this milestone.
- **M2M — remote operations completion.** A consolidated status overview, the workspace dashboard
  in the existing PWA, deterministic diagnosis synthesis over M2K's reason codes, and retry UX over
  idempotent replays. The PWA and main API stay tailnet-private.

**Before M2J PR1 merges:** the supervised pass over the inherited live-validation debt ran on
2026-08-11 (D-2026-08-11-12). The blocking set — lifecycle, authority, capability truthfulness and
deployment integrity — is **cleared**; the remaining media-feature walkthroughs are deferred and
**do not block** the merge (D-2026-08-12-1). States are recorded item by item
[below](#inherited-live-validation-debt).

Two **parallel tracks**, isolated from production and outside the milestone gates — neither is
started: **Track B**, browser-actuator feasibility comparing Playwright, Kimi WebBridge and
BrowserSkill on one narrow user-triggered spike, semantic automation only, dedicated automation
profile; and **Track D**, Ollama host provisioning plus a Cofferdam-specific planner benchmark
whose numbers must exist before M2L's model choice is frozen. Qwen3.5-9B quantized is the current
candidate, not an architectural dependency; real and private fixtures stay local-only for now.

Later, unordered: **M2N** richer Markdown memory retrieval (backlinks first, vectors second) ·
**M2O** browser and desktop skills productized from Track B · **M2P** Codex app-server as a second
delegated worker and reviewer · **M2Q** fast/deep planner routing, only on Track D evidence ·
Guardian/Supervisor and Runtime A/B slots with the manual recovery command surface · update records
and the A/B self-update demonstration · process, window and display control · an optional OpenClaw
client.

### Inherited live-validation debt

The supervised pass ran on **2026-08-11**. Its results are below with explicit states, and the
blocking scope is now split by blast radius (D-2026-08-12-1): the core lifecycle, authority and
deployment items **block** M2J PR1 and are cleared; the remaining media-feature walkthroughs are
**deferred, non-blocking** debt and are tracked here rather than absorbed into a later milestone.

States are exact. `DEFERRED_NON_BLOCKING` is **not** a pass, and neither is
`BLOCKED_BY_PREREQUISITE`.

| Item | State | Evidence | Blocks M2J PR1? |
|---|---|---|---|
| M2B logout/login cycle — lifecycle at GDM and across a real login | **PASS** (2026-08-11) | 475 samples, 40 min, real GNOME logout → GDM → login: `MainPID` 43344 and `NRestarts=0` unchanged throughout, `/healthz` 200 in every sample, `Wants=`/`BindsTo=`/`PartOf=` empty in every sample | No |
| Graphical-session capability reporting before and after login | **PASS** (2026-08-11) | `open_application`/`open_url` observed `true → false → true` tracking the real session; **zero** mismatches against gnome-shell presence across 475 samples; GDM's own greeter session correctly not claimed | No |
| Authenticated/unauthenticated boundary across the cycle | **PASS** (2026-08-11) | unauthenticated `/api/status` returned **401 in all 475 samples**, including every pre-login sample | No |
| Production integrity and non-mutation during the cycle | **PASS** (2026-08-11) | slot commit, registry hashes and all three service `MainPID`s unchanged; tasks/events/turns `25/473/3` identical before and after; no provider helper spawned | No |
| M2C audio **write** path (set volume, mute) | **PASS** (2026-08-11) | bounded write validated against independent `wpctl` observation; state restored; invalid writes refused `422`, unknown resource `404` | No |
| M2C output **switching** | `BLOCKED_BY_PREREQUISITE` | host has one usable audio output; `move_audio_stream` unavailable. Not a pass | No |
| M2D.1 cold-start Play-now recovery | **PASS** (2026-08-11) | single operation `spop-3a0cbdd65646`: Spotify absent beforehand, launch triggered *inside* the operation, real Connect device appeared, exact selected result confirmed playing, 9.36 s, no second dispatch | No |
| Media provider credentials | **PASS** (2026-08-11) | Spotify and YouTube both configured and returning real catalogue results; Spotify OAuth connected with all required scopes. Supersedes the "unconfigured" text below | No |
| M2D / M2D.1 remaining transport, queue and volume walkthrough | `DEFERRED_NON_BLOCKING` | not executed; peripheral media-feature debt (D-2026-08-12-1) | No |
| M2D Spotify device transfer | `BLOCKED_BY_PREREQUISITE` | only one Connect device exists on this host. Not a pass | No |
| M2E YouTube player live walkthrough | `DEFERRED_NON_BLOCKING` | not executed; the endpoint exists and reports `disconnected` truthfully with no player open | No |
| M2D/M2E validation drop-in instructions | `DOCUMENTATION_STALE` | the `90-`/`95-` drop-ins point at unmerged feature clones and **must not be applied to production** — see below | No |

**The `90-`/`95-` validation drop-ins must not be applied.** They were written for a pre-merge
runtime that pointed the live service at unmerged feature clones. Production now runs the merged
A/B slot deployment (slot `a`, plus adapter drop-ins only). Re-applying them would recreate the
production-drift class M2H PR4 removed and `test_deployment_drift.py` guards. The affected
checklists carry the same warning at the top.

**Separately tracked, not part of this debt:** `open_media_provider` reports failure when Spotify
is already running (the snap's second instance exits immediately). It fails closed and truthfully
rather than fabricating success, so it is a low-priority defect **candidate**, not a blocker, and
no fix is attempted here.

### Operations debt

| Item | State | Observed | Blocks the roadmap? |
|---|---|---|---|
| cloudflared tunnel HA/edge connection flapping — investigate separately | `OPEN_NON_BLOCKING` | Chronic HA connection churn in `cofferdam-actions-tunnel.service`, noted during the M2J PR3 deployment and **pre-existing** — it predates PR3 and is **not attributed to M2J**. Re-observed during the PR4 deployment: a brief edge-reconnect window returned Cloudflare 530s and then **self-recovered** to a byte-identical response, with the connector process neither crashing nor restarting. The tunnel serves successfully outside those windows, so the external Actions surface remains usable. **Root cause unknown.** | No |
| Context transport bounds are not harmonized | `OPEN_NON_BLOCKING` | Three deliberately different bounds sit on one path: the projection **content** budget is 16 KiB, the workstation **transport** ceiling is 128 KiB, and the Actions bridge applies its own 60 KiB. The **effective external bound is therefore 60 KiB** — the bridge binds first. All three **fail closed** and **none truncates**: over-bound is refused (`response_too_large` on the daemon, a 500 on the bridge) rather than sliced, because half a JSON document is worse than an error. Recorded as design debt to revisit deliberately, **not** an M2K blocker and not to be "fixed" by quietly aligning the numbers. | No |

**No cause is claimed.** The churn has not been attributed to the ISP, to Cloudflare's edge, to
local networking or to the service unit, because nothing here probed any of them — recording an
observation is not the same as diagnosing it, and a guess written down becomes a fact somebody
cites later. It gets a dedicated audit or it stays open.

## Deferred (preserved, not on the critical path)

- Trust Core completion: finishing/reviewing/merging PR3c2, PR3d hash-chained audit log, PR4
  hardening. The module is preserved for future privileged-action and high-assurance-update use.
- Council/multi-model review integration, voice and wake words, native mobile apps, generalized
  multi-agent orchestration.
- **Vector/advanced memory** — deferred as an *index*, not as memory: M2J reads canonical Markdown
  with no retrieval layer at all, and M2N adds backlinks before embeddings. A derived index is
  never canonical (D-2026-08-08-6).
- **"Obsidian integration"** is no longer the right phrase for what is deferred. Reading and
  proposing edits to an Obsidian-**compatible** vault (plain CommonMark, `[[wikilinks]]`, optional
  frontmatter) is M2J. What stays deferred is integration *with the Obsidian application* —
  Cofferdam never invokes it, never reads `.obsidian/`, and never writes its config.
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
