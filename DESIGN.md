# Design

The public design doc for Cofferdam: the personal, always-on AI workstation. For the decision
record behind this design see [`DECISIONS.md`](DECISIONS.md); for delivery order see
[`ROADMAP.md`](ROADMAP.md). The Trust Core module retains its own detailed docs
([`THREAT-MODEL.md`](THREAT-MODEL.md), [`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md)).

## What Cofferdam is

One always-on Ubuntu Desktop host, controlled from a phone or tablet through a Cofferdam-owned
PWA, able to run applications, media, and AI coding tasks — and able to update *itself* through
a supervised candidate-build/test/activate/rollback loop. Semantic, typed controls are the
product; raw remote-desktop streaming is not (an existing remote-desktop tool is an acceptable
fallback for raw screen access).

## Architecture

```
phone/tablet (PWA over Tailscale)
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Guardian (small, stable, non-AI)                    │
│  slot registry · health checks · traffic switch     │
│  rollback · update/log record custody               │
└──────┬──────────────────────────┬───────────────────┘
       │ active                   │ candidate
       ▼                          ▼
┌──────────────────┐      ┌──────────────────┐
│ Runtime slot A   │      │ Runtime slot B   │
│  API + PWA       │      │  (being built /  │
│  action router   │      │   under test)    │
│  task/update     │      └──────────────────┘
│  model, adapters │
└──────┬───────────┘
       ▼ adapter interfaces (Cofferdam-owned)
  Claude Code · desktop/display/process · screenshots ·
  browser (Playwright, persistent profiles) · media ·
  files · Ollama (intent) · OpenClaw (optional accel)
```

The planned extension of this picture — a local planner that plans rather than implements, cloud
workers that implement, bounded actuators, and canonical Markdown memory — is
[Local-first orchestration](#local-first-orchestration-planned-recorded-2026-08-11) below. It adds
components; it changes none of the authority rules in this section.

### Session lifecycle boundary (binding)

Cofferdam runs as a systemd **user** service and reaches the desktop through the systemd **user
manager**, never by owning the desktop itself.

**Cofferdam observes and follows the graphical session. It never creates, fakes, starts, stops,
restarts, terminates, or owns one.** This is a hard boundary, not a style preference: violating
it once already left the host unable to complete a graphical login at all (`DECISIONS.md`
D-2026-08-04-1).

What follows from it:

- The service is **headless**. It starts at boot through lingering, survives logout, and never
  declares a dependency on `graphical-session.target` — `Wants=` is an *activation request*, not
  a wait, so naming it from a pre-login unit activates it with nothing behind it.
- "Is there a desktop?" is answered by a **read-only query at request time** — never from the
  service's own start-time environment (which under lingering predates the session entirely), and
  never from the presence of lingering.
- Applications are launched as **transient units of the user manager**, not as children of the
  service. The manager holds the real session environment — GNOME imports
  `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY` into it at login — and each application lands in its
  own cgroup, so restarting Cofferdam never kills the user's browser.
- Before a session exists, GUI capabilities are reported **false** and GUI actions are refused.
  Nothing is ever reported as succeeded, and no fake session is created.

Full lifecycle, migration, rollback, and TTY recovery:
[`docs/SERVICE_LIFECYCLE.md`](docs/SERVICE_LIFECYCLE.md).

### Guardian / Supervisor

A deliberately small, boring, non-AI process. It:

- knows which slot (A/B) is active; starts, stops, and health-checks runtimes;
- launches a candidate runtime on its own port, runs health/smoke checks, switches traffic on
  activation, detects obvious post-switch failure, and rolls back;
- preserves logs and update records outside the slots so no runtime version can rewrite history.

**Manual recovery is a design requirement, not a fallback.** Active-slot state lives in a plain,
human-readable file (`state/active-slot.json`), and a small set of shell commands —
`cofferdam-status`, `cofferdam-pin-slot a|b`, `cofferdam-rollback`, `cofferdam-start`,
`cofferdam-stop` — operate the slots over local shell or Tailscale SSH with **no AI, no runtime
UI, and no Guardian process required**. Runtime slots are ordinary systemd units, so a broken or
absent Guardian can never make the previous runtime impossible to start by hand. See
[`ROADMAP.md`](ROADMAP.md) M5 and `docs/recovery.md`.

Guardian contains **no product intelligence** and exposes only a narrow local control protocol
to the active runtime (request-candidate-start, report-status, request-activation — activation
itself requires the user or an explicitly chosen low-risk policy). The active runtime cannot
modify Guardian: Guardian code lives outside both slots, and Guardian verifies its own file
integrity as a deterministic check. Guardian updates use a stricter, separate, initially manual
path — **no automatic Guardian self-modification in the MVP.**

### Runtime slots A/B

Two replaceable checkouts of the Cofferdam application. One active, one inactive/candidate.
All development — human- or agent-driven — happens in the inactive slot. The candidate starts
on a separate port, is tested while the active runtime keeps serving, and becomes active only
through Guardian. The displaced version remains on disk for rollback until the new one is
proven stable.

### Separation of concerns on disk

Runtime code, persistent state, secrets, update records, logs, and user files never mix:

```
~/cofferdam/
  guardian/          # Guardian code + config + bin/ recovery commands (not writable by workers)
  slots/a/  slots/b/ # runtime checkouts (git worktrees)
  state/             # persistent app state + active-slot.json (plain, inspectable, hand-editable)
  secrets/           # tokens, API keys (0700; never in git, never sent to models)
  updates/           # update records (JSON, append-only, owned by Guardian)
  logs/              # guardian + per-slot logs
  profiles/          # browser profiles (e.g. media profile with Netflix login)
  files/             # user-controlled files exposed to the files adapter
```

### Cofferdam Core (owned, canonical)

The PWA UI; the task and update models; typed action schemas; host/device/display state; the
Guardian protocol; A/B update state and history; task cards; authorization categories; and the
adapter interfaces. **No external tool's schema — OpenClaw's included — is ever the canonical
data model.**

### Three layers, kept apart

Everything the runtime slot knows about the machine sits in exactly one of three layers.
Conflating them is how configuration starts lying about hardware:

1. **Definitions** — code-owned. Which applications exist as a concept, which launch adapters are
   permitted, which executables each may resolve to. Configuration selects among them and can
   never add one. `cofferdam/workstation/adapters/`.
2. **Runtime resources** — discovered from the machine at a stated instant: connected displays,
   running processes, application instances, windows. `cofferdam/workstation/runtime/`, described
   in [`docs/RUNTIME_INVENTORY.md`](docs/RUNTIME_INVENTORY.md).
3. **User overlays** — optional labels, aliases, preferences, policy metadata.
   `cofferdam/workstation/registries/`.

A definition being available does not mean it is running. A browser profile existing does not mean
a browser process or a tab is open. A runtime item comes from a current observation of the system,
and an empty or unavailable inventory stays empty rather than being filled with examples.

The ordering is binding: **discover the resource first, then attach the optional label.** A label
invented before the resource is a guess that will quietly disagree with the machine.

The desktop and mobile clients consume the **same** backend resource model. The model is the
contract; the clients are views of it.

### Typed actions and routing

Natural language becomes Cofferdam-owned typed actions (e.g. `open_application`,
`search_and_open_media`, `start_or_message_agent_task`), validated against schemas before
execution. Three routing levels:

1. **Deterministic** — buttons and exact commands; no model involved.
2. **Intent classification** — Ollama or a small API model maps a request to one typed action
   with arguments; uncertain classifications fall back to showing choices to the user.
3. **Delegated work** — development/research handed to Claude Code (or another worker).

Models never invent and execute unrestricted shell commands; the executor only runs typed
actions through adapters. Externally prepared prompts (e.g. from ChatGPT) can be pasted in.

### Adapters (replaceable by design)

Clean interfaces for: Claude Code, OpenClaw, Ollama/local models, browser automation, Ubuntu
desktop control, displays, process management, screenshots, media playback, and files. OpenClaw
is optional acceleration behind `OpenClawRuntimeAdapter`; the contract is that swapping to a
`NativeRuntimeAdapter` must not touch the UI, task records, update records, action schemas,
Guardian, A/B deployment, or user-facing behavior.

Browser and media automation uses a **dedicated persistent browser profile** under `profiles/`:
the user logs in to a service manually, once, on the desktop, and the session persists in that
profile. **Account passwords are never stored in the repository or in any config file, and are
never sent to a model.** Profile directories are treated as secrets — gitignored, kept out of
any backup that leaves the host, and never attached to an update record.

### Self-update loop (flagship)

The first demonstrated update is deliberately a **stateless UI change** (the system-clock card):
no schema or data migration, no Guardian modification, no secret-format change, no
package-manager change, no destructive filesystem operation. Those categories become eligible
only after the plain loop is demonstrated end-to-end, and each carries its own focused review.

A user update request is stored as an **update record** (original prompt, acceptance criteria,
target, candidate slot, worker, status, changed files, test evidence, health evidence,
activation decision, rollback state, outcome). A worker implements it **only in the candidate
slot**; deterministic tests and smoke/UI checks run; an advisory model review may comment but
can never substitute for tests; the user sees request-vs-change and evidence, then activates;
Guardian switches, monitors, and rolls back on failure. Cofferdam's claims are evidential, not
proof: "verified against recorded acceptance criteria", "passed deterministic tests", "matched
expected UI evidence" — never "proven correct".

## Local-first orchestration (planned; recorded 2026-08-11)

**None of this section is implemented.** It records the architecture the M2J → M2K → M2L → M2M
sequence builds toward, decided as [`DECISIONS.md`](DECISIONS.md) D-2026-08-11-1 … -12. It is an
*extension* of what ships today, not a redesign: every authority rule below is already enforced in
production for at least one surface, and the work is to make new components obey the same rules.

### The workstation is a personal private server

The user leaves the machine at home and reaches it from a phone, a browser, another computer or a
future native app. **The host remains the authority; every remote device is a control surface.**
Execution, memory, credentials, workers, browser automation and local models stay on the host
wherever practical. The PWA and main API stay tailnet-private, the private Custom GPT stays a
bounded conversation surface through the Actions bridge, and no generic public shell, filesystem
or browser-control surface exists.

### Three roles: planner, workers, actuators

```
  user (phone / browser / desktop — control surfaces, never authority)
        │
        ▼
  LOCAL PLANNER  (small local model, advisory)
   understands messy Turkish/English · holds the planning conversation
   drafts worker prompts and follow-ups · reads evidence · explains · recommends
   writes no code · runs nothing · holds no credentials
        │  proposals only — schema-validated, user-confirmed
        ▼
  existing validated paths (POST /api/tasks · clarification answer · typed action)
        │                                  │
        ▼                                  ▼
  CLOUD WORKERS                      ACTUATORS (local, typed)
   Task Core + TaskAdapter            launch · media · audio · displays
   claude-code · claude-agent-sdk     future: BrowserActuator, own process,
   codex (later)                      own profile, provider-neutral
```

**The planner is not the implementer.** It is planner, prompter, evaluator, coordinator and
conversational interface; implementation stays delegated to cloud workers through Task Core. The
worker abstraction is the one that already exists — `TaskAdapter` + declared capabilities +
`delegated_adapter` — and Codex is a third adapter rather than a new layer above it.

**Planner output is advisory and never authority.** Its entire output surface is conversation text
plus schema-validated proposals, and a proposal becomes real only through a path that already
validates and confirms. The planner lives in the daemon (`cofferdam/workstation/planner/`) and
talks to a **separate model-runtime process** on loopback through a replaceable provider client; it
is not a `TaskAdapter`, and it does not reach Task Core through the Actions bridge. Task Core stays
provider-neutral and model-free.

**The default loop is human-directed**: the user discusses, the planner drafts, the user confirms,
the worker implements, the planner evaluates, the user decides. Autonomous planner → worker loops
are not the recorded direction and would need their own decision.

### Three minds, three homes

Markdown is canonical memory (D-2026-08-08-6); derived indexes are rebuildable and never authority.

- **Global mind** — a dedicated, Obsidian-compatible, user-owned vault outside `$COFFERDAM_HOME`,
  read under an explicit host-owned grant. The architecture is not bound to a fixed absolute path.
- **Project mind** — the project's own repository. For Cofferdam itself the existing documents are
  role-mapped (`STATUS.md`, `ROADMAP.md`, `DECISIONS.md`, `DESIGN.md`); no `PLAN.md` is added to
  satisfy a filename convention, and workspace config records which file plays which role so the
  Context Builder reads roles rather than names.
- **Working Context** — active workspace, objective, active task, worker, plan checkpoint, pending
  decision, latest evidence reference, expected next step. This is **state, not memory**: SQLite
  under `state/`, never a second Markdown authority.

**No model silently writes durable memory.** The planner proposes, a person accepts on a
device-token surface, and Cofferdam applies atomically against the **base content hash** the user
reviewed — a drifted file refuses rather than overwriting. The planner and the Actions bridge have
no acceptance route at all, and deletion is never planner-proposable.

**Memory is related two ways, and both are required** (D-2026-08-12-4). Explicit links and
backlinks carry the relationships a person meant and wrote down. **Semantic retrieval** carries the
ones nobody thought to write: a new idea should reach the prior decisions and context it actually
relates to even when they share no words and no link, so the planner can raise a contradiction or a
decision the idea affects rather than waiting to be asked the right way. Backlinks first, vectors
second (M2N); neither exists yet.

Any such index — embedding, vector or full-text — is **derived, rebuildable, discardable,
provenance-preserving, local by default, and never canonical**. Deleting it removes recall and not
one byte of memory, and where it and the Markdown disagree the Markdown is right. Retrieval reads
only: a relationship it finds becomes durable memory through the same proposal-and-acceptance path
as anything else.

### Local context and external context are different objects

A pack assembled for the local planner and a pack **leaving the host** are two security objects,
not one type used twice: `LocalContextPack` and `CloudContextProjection`.

The local planner may receive rich local context — granted global mind, project mind, Working
Context, task state, evidence, preferences — because it runs on the authority and its provider
client speaks only to loopback. **Anything bound for a cloud worker, the private Custom GPT, a
browser skill or any other external model passes through an explicit egress projection.** By
default that carries relevant project plan and context, relevant decisions, the current objective
and acceptance criteria; it excludes global personal memory, unrelated-project memory, vault paths
and project filesystem roots, and credentials are structurally absent. Workspace policy may later
allow selected global-mind extracts by naming them — never by inference.

Each part of a pack carries `{source_kind, source_ref, observed_at}`, so the planner knows what is
an observation, what is a worker claim, what is memory and what is external text. Text read from a
web page or another model is `external_model_output`: data with provenance, never instructions.

The local half exists (M2J PR3): a deterministic builder over Working Context and role-addressed
mind, bounded by a UTF-8-byte budget, with every omission explained and no model, index or network
call in the path. `source_ref` is a semantic address such as `project:cofferdam:plan#m2j`, never a
filesystem location. See [`docs/CONTEXT.md`](docs/CONTEXT.md).

The outbound half is **M2J PR3.5** (D-2026-08-13-3): one narrow, versioned, deny-by-default
profile that decides eligibility on the decomposed semantic reference rather than on `source_kind`
— `global:preferences` and `project:cofferdam:status` are both `memory`, so a policy keyed on the
kind would publish personal memory through a diff that looked correct. All four Global Mind roles
are denied by default, including the two that are in every local pack; projected **text** is
sanitized as well as metadata, because canonical Markdown legitimately contains slot paths and
vault roots; and the object performs no network activity, because eligibility and transport are
separate questions. It is a hard gate on any PR4 surface. See
[`docs/CLOUD_CONTEXT_PROJECTION.md`](docs/CLOUD_CONTEXT_PROJECTION.md).

### Evidence outranks claims

**Adapter-reported evidence is a claim; only machine, git and Cofferdam observations are
observations.** Extended from single events to a whole turn, an `EvidenceBundle` keeps both and
flags the disagreement rather than reconciling it. Absent observation is `unknown` — rendered
*unverified*, never inferred and never reported as "did not happen".

Deterministic checks run before any model evaluation, and **the model layer may only downgrade,
never upgrade**: a criterion marked failed or unverified cannot become verified by model opinion.
Worker-reported success does not override missing evidence. Risk level is derived from code and
policy, never selected by the model; an LLM judgment may raise attention, never grant.

**Executable check text never comes from a request.** Checks are code-owned named checks or
host/operator-owned validated definitions referenced by stable id; the planner, the worker, a
remote caller and a task prompt never supply command text. Literal `argv`, no shell, validated
`cwd`, bounded timeout, bounded output.

### Health is observed before it is explained

The machine records structured reason codes first (`NETWORK_UNREACHABLE`, `PROVIDER_AUTH`,
`WORKER_EXITED`, `HOST_SHUTDOWN`, …); a deterministic layer diagnoses with a confidence word —
`observed`, `likely`, `unknown` — and the planner may then phrase it naturally. **The planner may
not invent operational truth**: "the worker likely stopped because this host lost connectivity" is
allowed where the probe failed; "your internet went down" is not, unless that was observed. When
evidence is insufficient, the honest sentence is that Cofferdam could not determine the cause.

Automatic retry covers idempotent reads and supervised infrastructure reconnects. **Consequential
operations are never retried automatically** — task creation, follow-ups, clarification answers,
actuator sends, memory applies — and user-triggered retries carry idempotency keys so a retry after
uncertainty is safe rather than duplicated.

## The Trust Core (preserved module)

The repository's existing code is the **Trust Core**: a deterministic, fail-closed,
zero-network, human-in-the-loop approval boundary for file changes (schema → guard → dry-run →
hash-bound single-use approval → byte-exact execution). It is complete through the interactive
approval mint on `main`, with the executor implemented on a work branch (see
[`STATUS.md`](STATUS.md)).

The 2026-08-01 pivot removes the Trust Core from the immediate critical path but **preserves
it, its history, and its frozen invariants** (see the reclassification table in
[`DECISIONS.md`](DECISIONS.md)). Its intended future role is the high-assurance authorization
layer for privileged operations: dangerous filesystem changes, system configuration, package
installation, Guardian updates, root-level operations, destructive migrations, external data
transmission, and exact change-set authorization. Whenever Trust Core code is touched, its
module-level invariants (deterministic guard, advisory-cannot-relax, fail-closed, I-16, …)
remain binding.

## Dependency policy

The Trust Core module stays standard-library, zero-network. The workstation product is
network-connected by nature and may take a small set of well-known, pinned dependencies
(e.g. FastAPI/uvicorn, Playwright); each addition should earn its place. Secrets never go into
git or into model prompts; the API surface is reachable only over the private network
(Tailscale) plus a device token.

## What this is not

Not a hosted service, not a multi-tenant product, not a subscription, not a general
remote-desktop protocol, not an autonomous system that modifies itself without a human
activation step (outside an explicitly chosen low-risk auto-activation policy).

## Provenance

Cofferdam is a clean-room implementation — see [`PROVENANCE.md`](PROVENANCE.md). The
multi-model review concept that influenced earlier planning is credited to
[karpathy/llm-council](https://github.com/karpathy/llm-council) (concept only).
