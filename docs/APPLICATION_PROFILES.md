# Application and browser-profile registries

Files:

```
$COFFERDAM_HOME/config/registries/applications.json
$COFFERDAM_HOME/config/registries/browser_profiles.json
```

Common envelope, ID and alias rules are in [`DEVICE_REGISTRY.md`](DEVICE_REGISTRY.md).

---

## `applications.json`

```json
{
  "id": "opera",
  "name": "Opera",
  "aliases": ["opera browser"],
  "enabled": true,
  "adapter_key": "opera"
}
```

`adapter_key` may be `opera` or `firefox` in M2A. That is the whole of what configuration gets to
say about an application.

### What configuration may not provide

Executable paths · command strings · argv · shell fragments · desktop-file paths · environment
overrides.

There is no field for any of them, unknown fields fail validation, and each of those names is on
the code-owned denylist so the refusal is explicit. **Executable and desktop metadata candidates
are code-owned allowlists** in the adapter, not configuration. A registry selects among
capabilities the code already has; it can never introduce one, and it can never point Cofferdam at
an arbitrary binary.

### Detection

Known Opera candidates, bounded and code-owned:

- executables: `opera`, `opera-stable`
- desktop entries: `opera.desktop`, `opera-stable.desktop`, `opera_opera.desktop`

Known Firefox candidates remain exactly as they were (`firefox`, `firefox-esr`).

Availability means **launchable**: an executable found on `PATH`. Desktop entries are searched
only in a fixed list of XDG application directories, and only to *explain an absence* — "installed
but not on PATH" and "not installed" need different fixes, and reporting the wrong one wastes the
user's time. Nothing is ever launched through a desktop file, and no path from a registry or an
API request is ever consulted.

`opera_opera.desktop` is listed because that is the real name Ubuntu's snap packaging uses. It is
in the table because it is a fact about the platform, not because desktop-file names are
configurable.

Opera is not assumed to be installed. If it is absent, the API and the PWA report it as
unavailable, and no action pretends otherwise. **Cofferdam never installs, updates, removes,
resets, or reconfigures a browser**, and never reads or copies a browser profile directory.

---

## `browser_profiles.json`

```json
{
  "id": "personal-opera",
  "name": "Kişisel Opera",
  "aliases": ["kişisel tarayıcı", "ana tarayıcı"],
  "enabled": true,
  "application_id": "opera",
  "default_for_url": true,
  "preferred_display_id": "large-monitor",
  "launch_mode": "default-instance",
  "domain_policy": { "mode": "allow-all", "domains": [] }
}
```

| field | rule |
| --- | --- |
| `application_id` | must reference an **enabled** application |
| `default_for_url` | at most one *enabled* profile may be `true`; none is also valid |
| `preferred_display_id` | optional; must reference a display when present |
| `launch_mode` | `default-instance` only, in M2A |
| `domain_policy` | `{ "mode": …, "domains": [...] }` |

### What a profile is, at this stage

`personal-opera` is a **semantic Cofferdam profile**. It selects:

- the Opera application,
- a URL policy,
- optionally, preferred-display metadata for future use.

It does **not** yet select a separate on-disk Opera profile. `launch_mode: "default-instance"`
means exactly what it says: the URL is handed to Opera the way any desktop launcher would, so an
already-running Opera opens it as a new tab in the session the user is already using. No second
isolated browser is started, no window is closed, and no existing tab is disturbed.

M2A must not accept or store: Opera/Chromium profile-directory paths · `--user-data-dir` · profile
names passed as command-line arguments · cookies · passwords · account identifiers · browser
tokens · extension secrets. No field exists for any of these.

`preferred_display_id` is **metadata only** in M2A. No window movement occurs; nothing in the
product reads it to place anything. It is recorded in the action result so the phone can show it,
labelled as metadata.

### Domain policy

Allow every HTTP/HTTPS host:

```json
{ "mode": "allow-all", "domains": [] }
```

Restrict to an allow-list:

```json
{ "mode": "allow-list", "domains": ["example.com", "youtube.com"] }
```

Rules:

- Only `allow-all` and `allow-list` exist.
- `allow-all` takes **no** domains; use `allow-list` to restrict.
- `allow-list` must contain at least one valid hostname.
- Entries are bare hostnames: no URL schemes, paths, ports, credentials, wildcards, or regex.
  Each is refused by name rather than stripped into something that looks like it worked. Use
  punycode for non-ASCII names.
- `example.com` allows `example.com` **and its subdomains**. It must not — and does not — allow
  `badexample.com`: matching requires either equality or a label-boundary dot, because the plain
  suffix check that would accept `badexample.com` is the classic allow-list bypass.
- URL scheme validation stays limited to HTTP and HTTPS, unchanged from M1.
- Disabled profiles cannot be selected.

---

## `open_url` with a browser profile

The typed `open_url` action gains one optional field:

```json
{ "action": "open_url", "params": { "url": "https://example.com", "browser_profile_id": "personal-opera" } }
```

### Selection

| request | result |
| --- | --- |
| explicit valid `browser_profile_id` | that profile's application |
| explicit unknown or disabled profile | **fails closed** — `browser_profile_invalid` |
| no id, exactly one enabled `default_for_url` profile whose browser is available | that profile |
| no id, otherwise | the pre-M2A legacy browser launch, unchanged |

**An explicit profile never falls back to another one.** Naming a profile is a statement about
which browser context may see the URL.

Domain policy is enforced **before** anything launches, and applies to whichever profile was
selected — explicit or default. It is checked *before* the availability check too, so a URL an
allow-list forbids is refused whether or not the browser happens to be installed. Falling back to
legacy behaviour can never become a way around a policy.

### Backward compatibility

A request carrying only `url` continues to work exactly as before. A machine with no registry
files has no profiles, therefore no default, therefore takes the legacy path.

Registries that exist but are **invalid** fail closed instead: with an unreadable policy the
honest answer is "I do not know what you allow", and guessing "everything" is the wrong way to be
wrong.

### Failure vocabulary

| code | meaning |
| --- | --- |
| `browser_profile_invalid` | no such profile, or it is disabled |
| `domain_not_allowed` | the selected profile's policy forbids that host |
| `application_unavailable` | the selected profile's browser is not installed/launchable here |
| `configuration_invalid` | local registry data exists but could not be validated |

### What is not regressed

Real-session detection, false-success prevention, `NoNewPrivileges` handling through transient
user units, HTTP/HTTPS-only validation, Tailscale-only binding, screenshot fail-closed behaviour,
and the no-shell boundary are all unchanged. Launching still goes through the verified Wayland
graphical-session launcher added in M1: a fixed argv handed to the systemd **user manager** as a
transient unit, watched for a settle window, and confirmed before being reported as succeeded.

No arbitrary executable, path, or argv data enters the action at any point. The profile resolves
to a **logical application key**; the adapter still owns the mapping from that key to a program.

### One adapter change M2A did need: Opera's delegation exit code

Launching `opera <url>` while Opera is already running prints *"Opening in existing browser
session."*, opens the tab, and exits **24** — Chromium's
`CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED`. systemd marks any non-zero exit as `failed`,
so the M1 launcher reported a tab that had visibly opened as *"the application exited immediately
instead of starting"*.

The launcher now takes a **per-application list of specific** exit codes it knows how to
interpret — `{"opera": (24,)}` — and treats such an exit as `exited`, *never* as running. That
matters, because `exited` is not success on its own: the adapter still requires a live instance of
the same application to be visible before it reports the launch as succeeded. So the M1 rule holds
unchanged — an exit code is never evidence by itself — while the one case where a non-zero exit
genuinely means "I handed it to the browser you are already using" stops being reported as a
failure. Every other exit status, and every other application, still fails closed.

Other Chromium-based browsers share the constant. They are deliberately not listed until the
behaviour has been observed on this host for them.
