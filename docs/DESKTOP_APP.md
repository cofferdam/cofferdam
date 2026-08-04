# ADR: a desktop companion for Cofferdam

**Status:** decided for now — recommend a thin **Tauri 2** desktop companion. Nothing is built in
M2A: no Rust, no Node, no Tauri scaffolding is added by this milestone. Revisit implementation in
M2B, after the registry/API foundation is merged.

**Context.** Cofferdam is controlled from a phone or tablet through a PWA served by the Python
daemon over Tailscale. Sitting at the workstation itself, three things are missing: a persistent
tray indicator, a place for a **local** approval prompt (the confirmation policy in
[`CONTROL_PLANE.md`](CONTROL_PLANE.md) needs somewhere on the desktop to ask), and OS deep links so
another application can hand Cofferdam a URL or a task.

**The constraint that decides everything.** The Python daemon stays authoritative and independent.
A companion is a *view*. If it closes, crashes, is uninstalled, or was never installed, the daemon
keeps running and the phone keeps working. Any option that would move authorization, state, or
adapters into the desktop process is disqualified before its other properties matter.

---

## Options

### 1. Installed PWA only

Install the existing PWA through the browser ("Install app" / `--app` window). No new code, no new
toolchain, no new package to ship.

### 2. Tauri 2 thin shell

A small Rust host process with a WebView2/WKWebView/WebKitGTK webview pointed at the daemon's
origin. Tray icon, autostart, native notifications, deep-link registration, single-instance
handling — all first-party plugins. The web assets are the ones the daemon already serves.

### 3. Electron (or a comparable lightweight wrapper: Neutralino, Wails)

A Chromium + Node runtime bundled with the app. Everything works, everywhere, predictably.

---

## Evaluation

| criterion | 1. installed PWA | 2. Tauri 2 | 3. Electron |
| --- | --- | --- | --- |
| **daemon independence** | total — no second process | total, if kept thin | total, if kept thin — but the bundled Node runtime is a standing temptation to move logic in |
| **Linux support** | good; install UX varies by browser | good — WebKitGTK on Linux; needs `libwebkit2gtk` present | good; ships its own Chromium |
| **tray icon** | **no** | yes (tray plugin) | yes |
| **autostart** | no (browser-dependent at best) | yes (autostart plugin, XDG autostart on Linux) | yes |
| **local approval prompts** | only while a tab is open and focused-ish | yes — tray + native notification + a window that can be raised | yes |
| **deep links** | no reliable desktop registration | yes (deep-link plugin, `.desktop` MIME registration) | yes |
| **reuse of existing frontend** | total — it *is* the frontend | total — same static assets, same HTTP/WS API | total |
| **package size** | ~0 | ~5–15 MB (system webview) | ~80–150 MB (bundled Chromium) |
| **security surface** | browser's own sandbox; nothing new | small: Rust host, no Node, capability-scoped plugin permissions, CSP-restricted webview | large: full Node runtime adjacent to the UI; needs `contextIsolation`, `nodeIntegration: false`, sandboxing discipline maintained forever |
| **maintenance burden** | none | a Rust toolchain and one more CI target; webview differences across distros | a Chromium runtime to keep patched, and its release cadence to follow |
| **recovery when the UI crashes** | reopen a tab; nothing was lost | the daemon is untouched; relaunch from tray/autostart | same, at greater weight |
| **future daemon communication** | fetch + WebSocket to the tailnet origin | identical — plus optional local IPC if it ever earns its place | identical |

### Reading the table

Option 1 is genuinely attractive and is what exists today. It fails on exactly the three things the
companion is for: **tray, autostart, deep links.** Those are not polish — a local approval prompt
that only appears if a browser tab happens to be open is not an approval mechanism.

Option 3 buys predictability with a bundled browser engine. For a UI that is a handful of static
files talking to a local daemon, ~100 MB and a Chromium security-update treadmill is a large
recurring cost for no capability Tauri lacks. The Node runtime sitting next to the UI is the more
serious objection: the whole design depends on the companion *staying* thin, and Electron makes
"just do it in the app" the path of least resistance.

Option 2 gives the three missing capabilities at roughly the size of a large image, with a host
process that has no scripting runtime to drift into. Its real cost is honest: a second language in
the build, and WebKitGTK behaving slightly differently from the phone's browser. Both are
acceptable for a shell whose entire job is to host a webview and own a tray icon. The Linux
`libwebkit2gtk` dependency is a packaging detail to verify on Ubuntu during M2B, not a blocker.

---

## Decision

1. **Recommend a thin Tauri 2 desktop companion**, scoped to: tray status, local approval prompts,
   settings, deep links, autostart, and single-instance behaviour.
2. **Keep the Python daemon authoritative and independent.** The companion holds no authorization
   logic, no adapters, no registries, no state of record. It talks to the daemon over the same
   authenticated HTTP/WebSocket API the phone uses.
3. **Add no Rust, Node, or Tauri scaffolding in M2A.**
4. **Revisit implementation in M2B**, after the registry/API foundation is merged — the companion's
   settings and approval screens are views onto exactly that foundation, and building the shell
   first would mean guessing at them.

### Invariants for whoever builds it

- Closing or crashing the companion **must never** disable phone access or the daemon. This is the
  acceptance test, not a design note: kill the companion, then drive the workstation from the phone.
- The daemon must never require the companion to be running, at any point, for any feature.
- The companion ships no second copy of a decision the daemon already makes. If it needs to know
  whether something is allowed, it asks.
- If local IPC is ever added, it is an optimisation over the existing API — never a privileged
  side channel that skips token authentication.

### What would reopen this

- Ubuntu webview packaging turning out to be genuinely painful across the distributions that
  matter (would favour Electron).
- The companion needing capabilities a webview cannot reach — global hotkeys and screen capture
  are the plausible candidates (would need re-evaluating, possibly against a native GTK shell).
- The PWA gaining reliable tray, autostart, and protocol-handler support on Linux (would favour
  option 1 and delete this decision entirely).
