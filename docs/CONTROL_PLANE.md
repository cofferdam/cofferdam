# The Cofferdam control plane

Cofferdam is becoming a **local, permission-bounded control plane** for one person's computing
environment: an Ubuntu workstation, future Raspberry Pi guardian/controller nodes, named displays
and the human phrases that refer to them ("büyük monitör"), allowlisted applications and browser
profiles, future Claude Code and other agent sessions, and — eventually — routing a conversation
started in a browser to an agent and back to where it came from.

**M2A is the foundation only.** It adds registries, a read-only API over them, browser-profile
awareness for the one action that can use it today, and the documents that fix the shape of what
comes next. It deliberately implements none of the following:

**runtime discovery of any kind** · Raspberry Pi control · Wake-on-LAN or any physical power
action · window movement or display placement · browser DOM access · ChatGPT or Claude web
automation · browser extensions · agent execution · Claude Code session execution · message
sending · natural-language action planning · desktop application scaffolding · any reboot
behaviour change.

And, as everywhere in this product: **no arbitrary shell execution**, at any layer, through any
schema — and **no pixel-coordinate automation**, ever.

> **M2A knows nothing about what is currently connected, running, or open.** It knows what the
> code can do (definitions) and what the user chose to call things (overlays). Discovering the
> live machine is the next milestone. Nothing in this repository ships pre-named as though a
> display, browser window, or process had already been found.

---

## Shape of the system

```
   phone / tablet  ──── Tailscale ────┐
        (PWA)                         │
                                      ▼
   future desktop companion ──►  Python daemon  ──►  adapters ──► the machine
     (thin, optional)             (authoritative)
                                      │
                                      ├── definitions  (code-owned: what CAN be done)
                                      ├── overlays     (registries: optional names + preferences)
                                      ├── inventory    (LATER: what is actually here right now)
                                      ├── policy       (what is allowed, what needs confirming)
                                      ├── state        (action records; later, task records)
                                      └── routing      (later: origin ⇄ agent ⇄ return route)
```

### The Python daemon is authoritative

The existing daemon owns authorization, action validation, state, routing records, and adapters.
Nothing else does. Clients — the PWA today, a desktop companion later, a browser extension one
day — are **views and input surfaces**. They may ask; they never decide.

This is not an aesthetic preference. The daemon is the only component that runs unattended, is
supervised by systemd, and will be supervised by Guardian. A permission decision made in a UI is
a decision made in a process that can be closed, crashed, or replaced by whatever the user
installed last.

### The PWA stays independently usable

Phone and tablet access is the existing PWA over Tailscale, and it remains fully usable **when no
desktop companion is running, installed, or even written**. Closing or crashing the companion
must never disable phone access or the daemon.

### The desktop companion will be thin

A future desktop application is a **thin companion**, not a second daemon: tray status, local
approval prompts, settings, and deep links. It reuses the same frontend and the same HTTP/WebSocket
API the phone uses. See [`DESKTOP_APP.md`](DESKTOP_APP.md) for the decision record and the
alternatives weighed.

---

## Three layers: definitions, runtime resources, user overlays

This is the distinction the whole product turns on, and getting it wrong makes configuration lie
about the machine.

### A. Definitions — code-owned, not configurable

- Allowlisted **application definitions**: `opera`, `firefox`. These say a concept exists and
  that the code knows how to launch it.
- **Launch adapters** and their **bounded executable candidates** (`opera`, `opera-stable`,
  `firefox`, `firefox-esr`) and desktop-entry basenames.

Definitions live in the source. Configuration selects among them and can never add one: no
executable path, argv, command string, shell fragment, desktop-file path, or environment
override is representable anywhere in a registry.

### B. Runtime resources — discovered, **not implemented yet**

What is actually here *right now*:

- currently connected displays
- currently running processes
- current application instances
- current windows
- later: browser tabs
- later: agent task instances

**None of this exists in M2A.** Cofferdam performs no runtime discovery today. That is the next
milestone — see *M2B — Runtime inventory* in [`../ROADMAP.md`](../ROADMAP.md).

### C. User overlays — the registry files

Optional names, aliases, preferences, and policy metadata. That is all a registry is.

### The rule that follows

**Registries must not pretend to be runtime discovery.** Writing `displays.json` does not make a
display exist, and reading it does not tell you what is plugged in. A display entry is a *label
waiting for a display*; a browser profile is a *launch preference*, not an open browser window; a
device entry is something you declared, not something Cofferdam found.

Ordering matters for everything after M2A: **discover the real resource first, then attach the
optional label.** Never the reverse. A label invented before the resource is a guess that will
quietly disagree with the machine.

Consequently, nothing ships pre-named. There is no `large-monitor`, no "main monitor", no
"laptop display", no `personal-opera`, no "backup browser" in this repository or in a default
installation. `examples/registries/` contains only entries prefixed `example`, nothing copies
them into `$COFFERDAM_HOME`, and a machine with no registry files is a fully working machine.

## Runtime resource identity — rules the inventory milestone must follow

Recorded now, while they are still free to keep. None is implemented in M2A.

- **A PID is visible and usable only together with process start time.** It is shown, and it can
  be acted on, but only after the start time has been re-verified.
- **A PID alone is never a stable resource identity.** PIDs are reused; a stale PID plus an
  action is how the wrong process gets terminated.
- **Application instance identity** = host/boot identity + PID + start time. The boot identity is
  what stops an identity surviving a reboot into a different process.
- **Display identity prefers a hardware fingerprint** — EDID (or its hash) plus the owning device.
  **Connector names such as `DP-1` are runtime hints**, not identity: they change when a cable
  moves.
- **Browser tabs get browser-extension tab IDs.** Tab identity comes from the browser's own API,
  through a permission-bounded companion extension. It must never be inferred from Chromium
  process IDs — a tab is not a process, and the mapping is neither stable nor observable.
- **User labels are overlays.** They may be attached when a resource is first discovered, or at
  any time later, and a resource without a label is completely normal.

## How Cofferdam is allowed to talk to the system

Prefer, in this order: official APIs · CLI protocols · MCP · browser extension APIs · D-Bus ·
systemd · desktop portals · semantic accessibility interfaces.

**Mouse-coordinate and screen-pixel automation is not an accepted core mechanism.** No
pixel-coordinate automation exists in M2A and none may be added: clicking at (x, y) is unverifiable,
silently breaks on any layout or resolution change, and produces exactly the false successes that
M1 was spent eliminating. Where a semantic interface genuinely does not exist, the answer is to
say the capability is unavailable — not to aim at a pixel.

## Naming: stable IDs, human aliases

Every registry item has an immutable ASCII kebab-case `id` and a human `name` plus `aliases`.
People say "büyük monitör"; references between registries use a stable ID. Aliases are resolved
*through the registries* — never guessed, never fuzzy-matched — and an ambiguous phrase is refused
rather than resolved to a coin flip.

An alias is a **label on something**. Until the runtime inventory milestone can discover a real
display, "büyük monitör" has nothing to be a label *of*, which is why no display ships pre-named.

Alias matching folds Unicode case, trims and collapses whitespace, and folds Turkish dotted and
dotless I together, so "MONİTÖR", "monitör", "IŞIK" and "ışık" behave the way a Turkish speaker
expects. The original text is preserved for display. Two items whose names or aliases normalize
to the same key are a **validation failure**: the registry refuses to load rather than let a
phrase mean two things.

## Configuration is validated files, not environment variables

Semantic machine configuration — devices, displays, applications, browser profiles, agent
profiles, route templates — lives in versioned JSON registries under
`$COFFERDAM_HOME/config/registries/`. Environment variables stay for *runtime* knobs (bind
address, port, adapter selection).

The reason is structure: registries carry stable IDs, Unicode alias lists, nested policy objects,
and cross-file references. `KEY=value` can encode none of that, and — more importantly — cannot
be *validated* as a whole. A registry either loads completely or not at all.

## What registries may never contain

No secrets, tokens, credentials, cookies, passwords, account identifiers, or browser profile
data. Not "should not" — **cannot**: no schema has a field for them, unknown fields fail
validation, and a code-owned denylist rejects the obvious attempts (`command`, `argv`,
`executable`, `path`, `user_data_dir`, `token`, `cookies`, …) by name so the refusal explains
itself.

Likewise no executable paths, command strings, argv, shell fragments, desktop-file paths, or
environment overrides. Executable and desktop metadata candidates are **code-owned allowlists**
in the adapters. A registry selects among capabilities the code already has; it can never
introduce one.

## Static configuration versus live state

Registries describe what exists. They are **not** a session store.

Live browser tab IDs, ChatGPT conversation IDs, running task state, agent output — these are
*runtime task state*, and they belong in task records, not in configuration. Mixing them is how a
config file quietly becomes a place where someone's private conversation identifiers accumulate.

Every future routed task will carry: a **task/correlation ID**, an **origin adapter**, an **origin
conversation key**, a **target agent session**, a **return route**, a **status**, and a **result**.
None of those exist in M2A — see [`AGENT_ROUTING.md`](AGENT_ROUTING.md).

## Adapters are replaceable; DOM automation is never the architecture

Browser DOM automation, if it ever ships, is one adapter behind a Cofferdam-owned interface —
replaceable, and never the core. The same holds for an Opera extension: a permission-bounded
*input* to the daemon, not a place where decisions move to.

## Confirmation is policy-driven

Sending messages, merging code, shutdown, reboot, destructive actions, and physical power control
require confirmation, driven by policy rather than by whichever UI happens to be in front of the
user. M2A implements no such action, and therefore no confirmation flow — it records the rule so
the first such action cannot be added without one.

---

## M2A surface

| what | where |
| --- | --- |
| registry files (optional; absent by default) | `$COFFERDAM_HOME/config/registries/*.json` |
| format illustrations, never copied anywhere | [`examples/registries/`](../examples/registries/) |
| schemas | [`DEVICE_REGISTRY.md`](DEVICE_REGISTRY.md), [`APPLICATION_PROFILES.md`](APPLICATION_PROFILES.md), [`AGENT_ROUTING.md`](AGENT_ROUTING.md) |
| code | `cofferdam/workstation/registries/` |
| API | `GET /api/registries`, `GET /api/registries/{registry_name}` — authenticated, **read-only** |
| action | `open_url` gains an optional `browser_profile_id` |
| UI | read-only registry cards; a browser-profile selector on Open URL |

There is no `POST`, `PUT`, `PATCH`, or `DELETE` registry endpoint in M2A. Nothing reachable over
the network can change which applications exist or which domains a profile may open. Editing is a
text editor plus a service that re-reads the files; an atomic writer exists (with tests) for the
milestone that adds editing, and is not wired to any route.

---

## The M1 reboot gate is still open

M1's post-reboot auto-start validation has **not** been performed. M1 must not be described as
fully validated, reboot-validated, or complete, and M2A does not change that. See
[`../STATUS.md`](../STATUS.md).
