# Cofferdam

**An open-source, personal, always-on AI workstation — an Ubuntu desktop you control from your
phone, that can safely improve itself.**

Cofferdam turns one Ubuntu Desktop machine into a personal server with a graphical desktop and
multiple displays, controlled from a phone or tablet through a Cofferdam-owned responsive
web/PWA interface. From the couch or away from home, you can check that the host is healthy,
open applications, take a screenshot where the desktop session allows it, send a YouTube video
to the second display, start a
Claude Code task in a project, watch its progress as a simple status card — and ask Cofferdam
to add a feature *to itself*, watch the candidate version get built and tested, activate it,
and roll it back if it misbehaves.

A cofferdam holds back the water to create a dry, safe worksite where real work gets done.
Here, the worksite is your own machine: AI does the work inside a boundary, and changes to the
system itself only cross that boundary through testing, explicit activation, and rollback.

## How it is built

- **Guardian** — a small, stable, non-AI supervisor. It starts and stops the runtime, manages
  two runtime slots (A/B), health-checks candidate versions, switches traffic, and rolls back.
  It contains no product intelligence and cannot be modified by the running AI.
- **Runtime A / Runtime B** — two replaceable application slots. One is active; the other is
  the candidate where coding agents implement requested updates. The previous version is always
  retained for rollback.
- **PWA control UI** — served by Cofferdam itself, reachable from phone/tablet over a private
  network (Tailscale).
- **Typed actions** — natural-language requests become Cofferdam-owned structured actions
  (`open_application`, `search_and_open_media`, `start_or_message_agent_task`, …). Models
  classify intent; they do not execute arbitrary shell commands.
- **Replaceable adapters** — Claude Code, browser automation, desktop/display control, media,
  files, and optionally OpenClaw (acceleration) and Ollama (local intent routing). Adapters are
  interfaces owned by Cofferdam; every external dependency is replaceable.

See [`DESIGN.md`](DESIGN.md) for the architecture, [`ROADMAP.md`](ROADMAP.md) for milestones,
[`STATUS.md`](STATUS.md) for what exists today, and [`DECISIONS.md`](DECISIONS.md) for the
decision record, including the 2026-08-01 pivot.

## Status

**Pre-MVP.** The repository currently contains the **Trust Core** — a deterministic, fail-closed,
human-in-the-loop approval boundary for file changes (guard → dry-run → hash-bound approval),
built model-free and zero-network. The Trust Core is preserved and remains valuable as a future
high-assurance authorization layer for privileged operations, but it is **not** the product's
current critical path. The workstation product described above is being built now; the first
milestone (M1) is a phone-reachable control surface on Ubuntu: live host status, screenshots,
launching a browser, and opening a URL on the host — surviving reboot unattended.

M1 is **implemented and validated on the real Ubuntu host** (GNOME/Wayland): the service, typed
actions, authentication, live events, and the PWA all run; application launch and URL opening
were performed from the phone over the tailnet; and the systemd unit survives logout, login, and
repeated reboots with the API reachable before anyone logs in. Two corrections came out of that
validation and are merged — a login-lifecycle regression (M1.1) and untruthful screenshot
capability reporting (M1.2).

Two limits are worth stating plainly rather than discovering later. **Screen capture does not
work under Wayland on this host**, so `screenshot` reports `false` — that is the truthful answer,
not a bug, and a Wayland-capable capture path is future work. And a **full Tailscale-outage test**
end to end has not been run; only the bounded bind-wait logic is verified.

This is a personal-first project: it must be useful to its maintainer before anything else.
There are no plans for subscriptions, hosted plans, or enterprise features.

> **Early-stage software.** Cofferdam makes **no claim of production-grade security** and no
> claim of complete Ubuntu support. It is a personal tool under active development, meant to run
> on a private network (Tailscale) behind a device token — never exposed to the internet. Do not
> deploy it where its failure or compromise would matter.

## What Cofferdam is not

- **Not a remote-desktop protocol.** Cofferdam offers semantic, typed controls that it owns; it
  does not implement screen streaming or input injection. An existing remote-desktop tool can sit
  beside it as a fallback for raw screen access.
- **Not a hosted or multi-tenant service**, and not a subscription product.
- **Not autonomous.** A self-update reaches the active runtime only through an explicit human
  activation step (or a policy the user deliberately chose), and rollback is always available.

## Platform

The first supported host is **Ubuntu Desktop** (running continuously, logged-in graphical
session). Windows and macOS hosts are deferred — the Windows adapter that exists is a
development convenience, not a supported deployment target. Clients are anything with a modern
browser.

## For coding agents

If you are an AI coding agent working in this repository, read [`AGENTS.md`](AGENTS.md) first.
In short: work only in candidate slots/worktrees, never touch the active slot or Guardian
without explicit scope, preserve update records and acceptance criteria, run the tests, and
never bypass activation/rollback procedures.

## Documentation

- [`DESIGN.md`](DESIGN.md) — architecture and design principles.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development setup, safety rules, dependency policy.
- [`ROADMAP.md`](ROADMAP.md) — milestones and open technical questions.
- [`STATUS.md`](STATUS.md) — what is merged, uncommitted, planned, deferred, superseded.
- [`DECISIONS.md`](DECISIONS.md) — decision record.
- [`AGENTS.md`](AGENTS.md) — rules for coding agents.
- [`THREAT-MODEL.md`](THREAT-MODEL.md) — Trust Core module threat model.
- [`SAFETY-AND-RISK.md`](SAFETY-AND-RISK.md) — Trust Core risk posture.
- [`SECURITY.md`](SECURITY.md) — vulnerability reporting.
- [`TESTING.md`](TESTING.md) — test strategy.
- [`PROVENANCE.md`](PROVENANCE.md) — clean-room provenance statement.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

Created and maintained by Efe Aydınalp.
