# Runtime inventory (M2B)

What is **actually connected and running on this machine right now** — the middle layer of the
three in [`CONTROL_PLANE.md`](CONTROL_PLANE.md). M2A knew what the code can launch (definitions)
and what the user chose to call things (overlays). This is the layer that looks at the machine.

Code: `cofferdam/workstation/runtime/`. API: `GET /api/runtime`. UI: the *Live system* panel.

**This milestone observes. It does not act.** Nothing here starts, stops, moves, reconfigures, or
terminates anything, and no route under `/api/runtime` accepts a write method. Process and window
control is a later milestone with its own identity re-verification rules.

---

## The four resources, and the honest answer for each

| resource | backend | status on the validation host |
| --- | --- | --- |
| connected displays | `mutter-displayconfig` + `drm-sysfs` | `ok` — 2 displays |
| running application instances | `systemd-cgroup` | `ok` |
| processes | `proc-filesystem` | `ok` |
| windows | none available | **`unavailable`, with a reason** |

## Collection status is a closed vocabulary

Every collection reports its own status, because the backends behind them fail independently:
Wayland can refuse to enumerate windows while `/proc` happily lists every process.

| status | meaning |
| --- | --- |
| `ok` | The backend ran and the items are the complete answer. **Zero items means the machine genuinely has none of that resource.** |
| `partial` | The items are real and something was missed. `warnings` says what. |
| `unavailable` | No backend on this host can answer at all. `reason` says why. There are **no items**. |
| `error` | A backend that should have worked failed. `reason` describes the kind, never a path or a trace. |

**The distinction between `ok`-and-empty and `unavailable` is what this milestone exists to
protect.** Reporting "no windows" when the truth is "this system cannot tell you about windows" is
the same false-success shape M1 found in the launch path and M2A found in the registries. The
model enforces it: an `unavailable` collection that carries items, or omits a reason, raises.

---

## Display discovery

### Why not `xrandr`

This host runs GNOME on Wayland. Under Wayland `xrandr` talks to XWayland, which reports a
*synthetic* layout maintained for X11 clients — derived from the compositor's real configuration
but not it. M1 used `xrandr --listmonitors` for a display *count*, and a count was the most it
could honestly support. It is not used here.

### `mutter-displayconfig` — primary, session-scoped

`org.gnome.Mutter.DisplayConfig.GetCurrentState` on the session bus: the compositor answering
about its own state. Supplies connector, manufacturer, model, serial, `is-builtin`, the human
`display-name`, the current mode and refresh rate, and — from the logical-monitor list — position,
scale, orientation, and which display is primary. Nothing else on this host can answer the layout
questions at all.

Called through `busctl --json=short` so the reply is parsed by `json` rather than by a hand-written
GVariant text reader. Read-only: `ApplyMonitorsConfig` and its relatives are never called.

### `drm-sysfs` — supplementary, hardware

`/sys/class/drm/*/` — the kernel's own view. Supplies the raw EDID block, which gives the hardware
fingerprint that display identity is built on, and the physical millimetres `GetCurrentState` does
not report. Read directly rather than through the compositor's deprecated `GetResources`.

Only the minimum EDID is parsed: a SHA-256 of the block, the manufacturer/model/serial triple, and
physical size. Everything else — timings, chromaticity, CEA extensions — is deliberately not
parsed, and a block that does not parse yields nothing rather than a guess.

### Joining the two

On the panel's own EDID-derived `(manufacturer, model, serial)` triple, **not** on connector names:
the kernel says `card1-HDMI-A-1` where Mutter says `HDMI-1`. Matching on content is exact; matching
on a name mapping is a guess maintained by hand. Name matching remains as a fallback and is
recorded in `match_method` when it was used.

Reproducing Mutter's exact fallback spellings is what makes the join reliable. A panel with no
monitor-name descriptor is reported by Mutter as `0x53ab` — its numeric product code in hex — and
the parser produces the same string, byte for byte.

### Display identity

| source | `resource_id` from | stability | when |
| --- | --- | --- | --- |
| `edid` | SHA-256 of the EDID block + host identity | `hardware` | the normal case |
| `edid-ambiguous` | the above, plus the connector | `weak` | two panels with byte-identical EDIDs |
| `connector` | connector name + host identity | `weak` | the EDID could not be read |

A hardware fingerprint survives a reboot, a cable moved to another port, and connector
renumbering — which is exactly what a user label has to survive to stay attached to the right
panel. A connector name is a **socket**: unplug one monitor, plug in another, and the name is
unchanged while the panel is not.

### Limitations

- Displays are **session-scoped**. Before a graphical login the collection is `unavailable`, not
  empty. The kernel would list connected connectors at that point, but "a panel is plugged in" and
  "the desktop is driving these displays in this layout" are different claims, and only the second
  is what the rest of the product means by a display.
- A connected panel the compositor has not enabled appears with `active: false`. A **disconnected**
  connector is not reported at all.
- Physical millimetres and the EDID fingerprint are absent for a panel whose EDID could not be
  read. Absent, not estimated.
- Two panels with byte-identical EDIDs (some models ship without a serial) stay two resources and
  are both marked weakly identified.

### Values are never invented

Manufacturer, model, and serial are reported exactly as the hardware described itself. A panel
that publishes no model name is described by its product code, and `model_source` says so, so a
client can tell a name from a number. Nothing becomes `"Unknown"`, `"N/A"`, or a plausible default.

---

## Process discovery

Backend: `/proc`, read directly. No `ps`, no subprocess, no shell.

### Identity

`host + boot + PID + start time`.

- **Start time** is `/proc/<pid>/stat` field 22, in clock ticks since boot, assigned by the kernel
  at fork and never changed. Two processes that reuse one PID have different start times with
  certainty.
- **Boot** scopes the tick count, which is meaningless across a reboot.
- **Host** scopes the boot.

**A PID alone is never an identity.** PIDs are recycled within minutes on a busy workstation, and a
stale PID plus an action is how a later milestone's "close this application" closes something else.
`start_ticks` is published so a control action can re-read it and compare before acting; that check
is what makes the identity worth having.

If the host publishes no boot identity, the collection is `unavailable` rather than falling back
to bare PIDs.

### What is never read

**`/proc/<pid>/environ` and `/proc/<pid>/cmdline` are not opened at all.** Both routinely carry
secrets on a real desktop — an API key passed as an argument, a token in an environment variable, a
database URL with its password, a file path revealing a document's name. Safely handling a secret
already in memory is a much harder problem than not reading it, so grouping is built on cgroup
membership and process ancestry instead, neither of which needs a command line.

This is asserted structurally over the package source as well as behaviourally over the output, so
"just this once, for classification" cannot creep back in.

### What is published

`resource_id` · PID · parent PID · `start_ticks` · `started_at` · `name` (from `comm`) ·
`executable` and `executable_path` (from `/proc/<pid>/exe`) · state · owning UID · systemd unit ·
cgroup path · the application instance it belongs to · backend.

### Failure tolerance

- A process that **exits during the scan** is omitted and does **not** degrade the collection. It
  is genuinely not running, which is what the snapshot should say. Marking every snapshot `partial`
  because a process exited would make `partial` meaningless.
- A process that **exists but cannot be read** does downgrade to `partial`, with a count. Something
  is there and we cannot describe it.
- A corrupt `stat` line, a missing `exe` link, and a permission error each cost one process, never
  the scan.
- Only processes owned by the user this service runs as are enumerated.

---

## Application-instance discovery

### Launchable is not running

Four distinct things, and none may stand in for another:

1. **application definition** — the concept exists and Cofferdam knows how to launch it;
2. **available to launch** — a definition whose executable was found on this host
   (`/api/status`, the *Configuration & templates* panel);
3. **running application instance** — actual processes, with verified PID **and** start time
   (`/api/runtime`, the *Live system* panel);
4. **current windows** — belonging to an instance (unavailable on this host).

Firefox being installed produces **no** entry in the applications collection until a Firefox
process exists.

### The instance boundary

A modern desktop application is not a process. Opera on the validation host is **19 processes**:
one browser process, a zygote, a GPU process, a network service, and a renderer per site. Listing
processes whose name matches `opera` would report nineteen running Operas.

The evidence used is **systemd cgroup membership**, because the system already computed it. When
anything launches a desktop application on a systemd session — GNOME Shell, a `.desktop`
activation, snap, or Cofferdam's own `systemd-run --user` — the application lands in its own
transient unit under `app.slice`, and every process it forks inherits that cgroup. All 19 Opera
processes sit in one `snap.opera.opera-<uuid>.scope`.

**The rule:** a running application instance is a `.scope` unit under `app.slice`, or a
`cofferdam-app-*.service` we started ourselves. Nothing else is promoted to an instance.

`.scope` is the discriminating part. `app.slice` also contains plain `.service` units —
`dconf.service`, `ssh-agent.service`, `gnome-keyring-daemon.service`, and this service itself —
which are session infrastructure, not applications the user opened. A scope is what systemd creates
for a process it did not fork itself: a launched application.

### Units that describe the same application

systemd names these `app[-<launcher>]-<ApplicationID>-<discriminator>.scope`. A GNOME launch
produces **two** scopes for one application — observed here as `app-com.anthropic.Claude-7358.scope`
and `app-gnome-com.anthropic.Claude-7358.scope`. Both encode the same application ID and the same
launcher PID, so they are parsed and merged on that pair. This is grammar, not substring matching:
two genuinely different applications never agree on both halves.

### Mapping to a definition

The only evidence is the **exact basename of a real executable path** from `/proc/<pid>/exe`, and
only the **root** process's. Matching on `comm` would be matching on a 15-character truncation a
process can rename at will. Consulting every member would let a bundled helper speak for the whole
application: an Electron application shipping a binary called `chromium` would be reported as
Chromium — wrong, and wrong in a way that looks entirely plausible on a card.

No match leaves `application_id: null`. **An unmapped instance is a fact; a wrong mapping is a bug
that looks like a feature.**

The launch table comes from the adapter's `application_executables()`, so discovery never hardcodes
a program name. It deliberately does not follow `/snap/bin/opera` to its symlink target
`/usr/bin/snap`, which would classify every unrelated snap helper as Opera.

### Grouping limitations

- **A D-Bus-activated application cannot be separated.** On the validation host the terminal
  (`ptyxis`) lives in `session.slice/dbus.service`, shared with every other D-Bus-activated
  program. A shared unit is not an instance boundary, so those processes stay unmapped rather than
  being fused into a fictional application. The shell running *inside* the terminal has its own
  `ptyxis-spawn-<uuid>.scope` and is discovered.
- **A browser tab is not an application instance** and is never inferred from renderer processes.
  Tab discovery needs a browser companion reporting real tab IDs; that is a later milestone.
- **Applications launched outside Cofferdam are discovered** — most of them are. Attribution is
  reported as `launch_source`, never used as a filter.

### Launch attribution is three-valued

`launch_source` is one of `confirmed_cofferdam`, `confirmed_external`, or `unknown`. It is **not**
a boolean, and that is a correction forced by live validation on 2026-08-05.

The field started as `launched_by_cofferdam: true|false`. A Cofferdam-issued `open_application`
for Firefox produced a correctly grouped instance — and reported `false`. Snapd had re-parented
the launch out of our `cofferdam-app-<hex>.service` into
`snap.firefox.firefox-<uuid>.scope` before the first scan, taking the evidence with it. A boolean
has nowhere to put "the evidence is gone", so it said the one thing that was definitely untrue:
that something other than Cofferdam had started it.

The rules now, in order:

| Evidence | `launch_source` |
|---|---|
| our `cofferdam-app-<hex>.service` is still the unit | `confirmed_cofferdam` |
| any `snap.<package>.<app>-<uuid>.scope` | `unknown` — always |
| a scope naming a desktop shell, `app-gnome-<AppID>-<pid>.scope` | `confirmed_external` |
| anything else | `unknown` |

Two properties are deliberate. **Snap always yields `unknown`**, even when another unit sits
beside it, because re-parenting is precisely what destroyed the evidence — a snap scope is equally
consistent with a Cofferdam launch and a user double-click. **The absence of our unit is never on
its own grounds for `confirmed_external`**; that inference is the bug. `confirmed_external`
requires a launcher to have *named itself* in the unit, which Cofferdam never does because
`systemd-run --user --unit=` creates a `.service`, not an `app-*.scope`.

The PWA shows a badge only for the two confirmed states. `unknown` reads *"launch source not
confirmed"* in the expanded facts and contributes no badge; it must never render as "not launched
by Cofferdam".

Attributing a launch across a snapd re-parent needs launch-time bookkeeping — record the PID and
start time we started and match it back on scan — rather than unit-name inspection. That is not in
M2B1.
- **Window counts are absent, not zero**, while window discovery is unavailable.

---

## Window discovery

**Status on this host: `unavailable`, with a precise reason.** The interface exists and is wired
into the snapshot; there is no backend behind it. That is a finding, not a stub.

### What was investigated (GNOME Shell 50.1, Wayland)

| interface | result |
| --- | --- |
| `org.gnome.Shell.Eval` | Present on the bus, **returns `(false, '')` for every expression** — disabled outside unsafe-mode in release builds. Also barred on our side: evaluating JavaScript inside the compositor is arbitrary code execution in the user's shell. It would not be used even if it answered. |
| `org.gnome.Mutter.DisplayConfig` | Answers fully, but its vocabulary is monitors and layout. It has no window concept. |
| `org.freedesktop.portal.*` | No portal enumerates windows. The portal surface grants a user-approved capability for a specific interaction, not a listing. The screenshot portal was already found on this host never to emit a `Response` even non-interactively. |
| AT-SPI accessibility | `org.a11y.Bus` is running, but `org.gnome.desktop.interface toolkit-accessibility` is **false**, so GTK applications do not export their trees. Enabling it is a change to the user's desktop configuration with a real cost — accessibility bridges are a documented way for one application to read another's contents — and is not a trade this milestone makes on the user's behalf. |

Deliberately not used: pixel matching, OCR, screenshots, fixed coordinates, blind clicking, and
installing a GNOME extension without the user asking for one. Each could produce a window list.
None produces *evidence*, and an extension is a persistent change to the desktop.

### The seam for later

`WindowDiscovery` is the interface a future backend implements. The expected shape is a **GNOME
companion extension the user installs knowingly** (see [`DESKTOP_APP.md`](DESKTOP_APP.md)),
exposing a narrow read-only D-Bus method that lists windows with their owning application's PID.
Adding it means adding a backend and returning `ok`; no other module changes, and the snapshot
shape already carries the fields.

If window titles are ever exposed they are treated as sensitive: never written to logs, never
persisted, and served only through the authenticated API. A window title is routinely a document
name, a message subject, or a customer's name.

---

## Overlay compatibility

A resource is **discovered first and labelled afterwards**, and removing the label leaves the
resource fully identified.

`OverlayResolver` reads the existing `displays.json` overlay registry and, where an overlay
unambiguously describes a display discovery actually found, fills that display's `overlay` field.
The display's `resource_id`, connector, manufacturer, model, serial, and EDID fingerprint are
untouched — the overlay payload has no field that could restate any of them.

### What counts as a safe match

Only the **EDID fingerprint** (`match.edid_sha256`), or a **full** `manufacturer` + `model` +
`serial` triple where all three were reported by the hardware. Both identify a physical panel.

`connector_hint` alone is explicitly **not** enough, and this is the point of the whole exercise.
`HDMI-1` is a socket. An overlay matched on the hint alone would silently move a user's label onto
a different monitor. The registry schema already calls that field a *hint*; this is where the word
is enforced.

Ambiguity fails closed: if two overlay entries match one display, or one overlay matches two
displays, no overlay is applied and `overlay_skipped` records why.

A broken or missing registry yields no overlays and no error. An unlabelled display is complete.

### What M2B2 adds

Label and alias **editing**, which this milestone does not have:

1. the user selects a discovered card in the PWA or desktop client;
2. they add or edit a label and aliases;
3. the overlay is written atomically, keyed by the resource's **stable** identity — the EDID
   fingerprint for a display — through the existing `write_json_atomic`;
4. the resource keeps its system identity; the label is layered on it;
5. a display later disconnected still has its overlay and stays distinguishable from a connected
   one, because the overlay is keyed to the panel rather than to the connector it was in.

Every discovered resource already carries an `overlay` field and a stable `resource_id`, so that
flow needs no change to the identity model.

---

## The snapshot

One observation of one machine at one instant.

```
version · observed_at · host · boot · session · collections{displays, applications, processes,
windows} · warnings
```

The three identities are not decoration. A client comparing two snapshots uses them to decide
whether a `resource_id` from the older one still means anything: a different `boot.boot_id`
invalidates every process identity, a different `session.session_id` invalidates every
session-scoped resource, and a different `host.host_id` invalidates all of it.

Identities are published as **derived fingerprints** — a domain-separated SHA-256 prefix of
`/etc/machine-id`, `/proc/sys/kernel/random/boot_id`, and the graphical session's activation
stamp. None is a secret, but all are stable global identifiers; the derived form keeps every
comparison property the product needs and gives up only cross-system correlation. The `source`
field says which was available, so a weaker identity (a hostname fallback) is visible rather than
assumed away.

### One scan per snapshot

The four backends are called once, together, from one walk of `/proc` and one query to the
compositor. Sub-endpoints slice that shared snapshot rather than collecting independently, so a
client can never assemble a picture whose displays came from one instant and whose processes came
from another.

### Caching and refresh

Short-lived — five seconds — and more a rate limit than a cache: a phone polling the live view
must not make the workstation walk its process table every few seconds. `observed_at` always shows
the age being served, and `?refresh=true` bypasses it for the refresh button.

The cache is invalidated by **identity**, not only by time. If the graphical session changed — a
logout and a fresh login — the cached snapshot describes a session that no longer exists and is
dropped however recent it is. This is what stops a disconnected display or an ended session's
windows being served as current.

---

## API

| route | auth | purpose |
| --- | --- | --- |
| `GET /api/runtime` | yes | the full snapshot |
| `GET /api/runtime/{resource_kind}` | yes | one collection, with the snapshot header |

`resource_kind` is one of `displays`, `applications`, `processes`, `windows`; anything else is a
404 that does not echo the request text. `?refresh=true` on either bypasses the cache.

Every route requires the device token. A runtime inventory is a list of what is plugged into
somebody's desk and what they have open — more sensitive than the registries, not less — so there
is no anonymous summary, and no scan happens before the token is checked.

**No route accepts `POST`, `PUT`, `PATCH`, or `DELETE`.**

With the stub adapter every collection reports `unavailable`. A simulated host has not been looked
at, which is a different statement from "looked at, found nothing".

---

## Lifecycle boundaries, unchanged

Every rule merged before this milestone still holds, and discovery is built inside them:

- Cofferdam never starts, stops, owns, or pulls in `graphical-session.target`. Session detection
  uses `show`/`is-active` only.
- The headless daemon runs before login; GUI-scoped collections are `unavailable` until there is a
  session. Processes are not session-scoped and remain available.
- Logout invalidates session-scoped inventory; a later login produces a fresh session identity.
- No arbitrary shell, no `shell=True`, no `os.system`. Subprocess use stays centralised in
  `adapters/base.py`; the D-Bus helper runs a fixed argv built entirely from module constants.
- No broad `pkill`/`killall` — this milestone terminates nothing at all.
- No secrets in logs, argv, environment output, registry files, or API payloads.
- No browser profile inspection.
- No false success: a backend that cannot answer says `unavailable`.

A discovery failure cannot crash the daemon, terminate GNOME, restart the user manager, prevent
login, or create a restart loop. Every backend degrades to a status and a reason.
