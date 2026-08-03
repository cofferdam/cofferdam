# Status

Accurate as of **2026-08-01** (the personal-AI-workstation pivot, [`DECISIONS.md`](DECISIONS.md)
D-2026-08-01-1). Update this file when a category changes, not on every commit.

## Merged (on `main`)

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

## Preserved on a branch, not merged

- **PR3c2 — Candidate-B byte-exact executor.** Preserved as a WIP commit (`419f90f`) on branch
  `pr3c2-candidate-b-execution`: `executor.py`, `execute_cli.py`, `execstate.py`, `postimage.py`,
  `platform_support.py`, the authoritative `diffcheck` parser, and their tests. **Incomplete and
  unreviewed** — not merged, not on the critical path, and not to be continued unless a task
  explicitly scopes it. Do not rebase or rewrite that branch.

## In progress

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

### OPEN RELEASE GATE — M1 post-reboot auto-start is NOT validated

**M1 must not be described as fully validated, reboot-validated, or complete while this gate is
open.** Everything recorded above was observed in a single continuously logged-in session. The
host has not been rebooted since the service was installed, so the following remain **unverified
by observation**:

- the systemd user service starting automatically after a cold boot, through lingering, with no
  human logging in first;
- `tailscaled` coming up before the service needs its address (the service previously died in a
  restart loop with `cannot assign requested address` when the Tailscale address was absent);
- the listener re-binding to the Tailscale address unattended;
- phone-over-tailnet access working after an unattended reboot;
- graphical-session detection reporting `open_application`/`open_url` as **false** before login
  and **true** once a desktop session exists — the linger-before-login path is covered by tests,
  not yet by a real boot.

Known factor: automatic login is **not** enabled on this host (`/etc/gdm3/custom.conf` has no
`AutomaticLoginEnable`), so after a reboot the API is expected to return while GUI capabilities
correctly report unavailable until someone logs in at the desktop. That expectation is untested.

**How to close it:** reboot the host without logging in, run the reboot section of
`docs/checklists/m1-ubuntu-validation.md`, then record the observed result here and in
[`ROADMAP.md`](ROADMAP.md). The reboot is deferred at the user's request because the workstation
is in active use; it is not blocked or waived.

## Planned (active roadmap — see [`ROADMAP.md`](ROADMAP.md))

- Guardian/Supervisor + manual recovery command surface, Runtime A/B slots, process/window/
  display control, browser/media control (YouTube, Netflix profile), Claude Code task adapter,
  update records, A/B self-update demonstration, natural-language intent routing (Ollama),
  OpenClaw spike.

## Deferred (preserved, not on the critical path)

- Trust Core completion: finishing/reviewing/merging PR3c2, PR3d hash-chained audit log, PR4
  hardening. The module is preserved for future privileged-action and high-assurance-update use.
- Obsidian integration, vector/advanced memory, council/multi-model review integration, voice
  and wake words, native mobile apps, generalized multi-agent orchestration.
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
