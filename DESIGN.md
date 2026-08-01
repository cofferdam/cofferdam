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
