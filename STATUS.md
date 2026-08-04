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

- **M1 — remote control skeleton.** Branch `feat/m1-remote-control-skeleton` (`80df242`).
  Backend service (auth, status, typed actions, screenshot/open-application/open-URL, WebSocket
  events), host adapter layer (Linux/X11 + Windows dev implementations), PWA, JSON persistence,
  systemd unit, Ubuntu host-setup runbook and validation checklist.

  **Validated on Windows (development host only):** 476 tests pass; the running service returned
  live host status, captured a real 3840×1716 PNG, launched a browser, opened a URL, streamed
  action events over WebSocket, and rendered correctly at phone (375×812) and tablet (768×1024)
  viewports. This proves the architecture and the typed-action path — nothing more.

  **NOT validated on Ubuntu — the milestone is not complete.** Unverified: every Linux adapter
  path (`gnome-screenshot`/`maim`/`scrot`/`import`, `xdg-open`, `xrandr`), X11-vs-Wayland
  behaviour, snap-packaged browser binary names, the systemd user unit, `loginctl enable-linger`,
  reboot survival, Tailscale binding, and real phone-over-tailnet access. Run
  `docs/checklists/m1-ubuntu-validation.md` on the real host; stub-adapter results do not count.

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
  isolation), and a known issue where screenshot capability is over-advertised before login —
  it fails closed rather than reporting false success, and is recorded but not fixed here.

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
