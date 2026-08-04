# Ubuntu host setup (M1)

How to turn an Ubuntu Desktop machine into the always-on Cofferdam host and
reach it from a phone. Executed against a real Ubuntu 26.04 GNOME/Wayland host
during the M1 validation run
([`checklists/m1-ubuntu-validation.md`](checklists/m1-ubuntu-validation.md)).
Correct this file with whatever further real runs teach.

## 0. Session type — Wayland is supported

Ubuntu's default GNOME **Wayland** session works for opening applications and
URLs. No login-screen change is needed.

What Wayland does restrict is **screen capture**: the compositor does not hand
an unattended background service a frame. Cofferdam reports this rather than
guessing — `/api/status` carries `session_type` and a `capabilities` map, and
the phone UI disables any control the host cannot currently perform. Screenshot
support under Wayland needs a capture tool that talks to the compositor; the
validation run did not find a working unattended path (see Troubleshooting), so
treat screenshots as unavailable on Wayland unless you have verified otherwise
on your own host.

Confirm what you are running:

```bash
echo $XDG_SESSION_TYPE
```

**Graphical actions require a logged-in desktop session.** The service is
started by lingering (step 7) and its API stays reachable from the phone even
before anyone logs in — but until a graphical session exists it reports
`open_application` and `open_url` as unavailable and refuses them, instead of
accepting a request that would go nowhere.

## 1. Keep the host awake

An always-on host must not suspend. The API itself does **not** need a logged-in
desktop — it stays reachable before login and reports GUI capabilities as false
until a real session exists — but graphical actions do.

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.session idle-delay 0
```

**Optional:** automatic login (Settings → Users → Automatic Login) brings the
graphical session back by itself after a reboot, so GUI actions become available
without anyone touching the machine. It is genuinely optional — the API and all
non-graphical functionality work without it — and it is a real security
trade-off, since anyone with physical access lands in your session. Cofferdam
never enables it for you.

## 2. Install dependencies

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git gnome-screenshot xdg-utils x11-utils firefox
```

`gnome-screenshot` is the preferred capture tool; `maim`, `scrot`, `spectacle`,
and ImageMagick's `import` also work — the adapter picks whichever it finds.

## 3. Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4          # note this address — the phone connects to it
```

The phone must be signed in to the same tailnet. This is the network boundary:
Cofferdam binds **only** to the Tailscale address, so nothing on the local LAN
or the public internet can reach it.

## 4. Install Cofferdam

The slot layout is forward-looking: M5 adds slot B beside slot A, so install
into `slots/a` now and no move is needed later.

```bash
mkdir -p ~/cofferdam/slots
git clone https://github.com/cofferdam/cofferdam.git ~/cofferdam/slots/a
cd ~/cofferdam/slots/a
python3 -m venv .venv
./.venv/bin/pip install -e ".[workstation]"
```

## 5. Configure

```bash
cp deploy/workstation.env.example ~/cofferdam/workstation.env
chmod 600 ~/cofferdam/workstation.env
# edit: set COFFERDAM_HOME to /home/<you>/cofferdam and
#       COFFERDAM_BIND_HOST to the tailscale ip from step 3
```

## 6. First run and the device token

```bash
cd ~/cofferdam/slots/a
COFFERDAM_HOME=~/cofferdam ./.venv/bin/python -m cofferdam.workstation
```

On first start it generates a device token, writes it to
`~/cofferdam/secrets/token` (mode 0600), and prints it **once to stderr**. Copy
it into the phone UI. To read it again later:

```bash
COFFERDAM_HOME=~/cofferdam ./.venv/bin/python -m cofferdam.workstation --print-token
```

The token is never returned by any endpoint and never written to the journal.

## 7. Install the service

Use the installer — it is idempotent, backs up anything it replaces, and
refuses to enable a unit that would break graphical login:

```bash
~/cofferdam/slots/a/deploy/install-workstation-service.sh --dry-run
```

```bash
~/cofferdam/slots/a/deploy/install-workstation-service.sh
```

```bash
loginctl enable-linger $USER     # reachable before login; survives logout/reboot
```

```bash
systemctl --user status cofferdam-workstation
journalctl --user -u cofferdam-workstation -f
```

> **Upgrading from the M1 unit?** Run the installer — it migrates you. The M1
> unit declared `Wants=graphical-session.target`, which under lingering
> activated that target at boot and made GNOME refuse every subsequent login
> ("A graphical session is already running!"). See
> [`SERVICE_LIFECYCLE.md`](SERVICE_LIFECYCLE.md) for the full analysis, the
> rollback, and TTY recovery.

To remove it again (this is also the rollback):

```bash
~/cofferdam/slots/a/deploy/uninstall-workstation-service.sh
```

## 8. Connect from the phone

Open `http://<tailscale-ip>:7101/` on the phone, paste the token, and add the
page to the home screen to install it as a PWA.

Verify it is really working, not stubbed: `/api/status` must report
`"adapter": "linux-x11"` and `"stub": false`.

## Dependency decisions (M1)

The Trust Core module stays standard-library-only. The workstation service
takes three runtime dependencies, all permissively licensed and widely used:

| Dependency | Why | License |
|---|---|---|
| `fastapi` | typed request validation (the mechanism that makes "no arbitrary commands" structural), native WebSocket support, testable without a running server | MIT |
| `uvicorn[standard]` | the ASGI server FastAPI needs | BSD-3-Clause |
| `psutil` | cross-platform host metrics without re-implementing `/proc` parsing per platform | BSD-3-Clause |

`httpx` is a test-only extra (`pip install -e ".[workstation,dev]"`).

This matches the R-2 baseline in [`../DECISIONS.md`](../DECISIONS.md) and the
dependency policy in [`../DESIGN.md`](../DESIGN.md): a small number of pinned,
well-audited dependencies outside the Trust Core path.

## Troubleshooting

**Real-run finding (M1 Ubuntu validation, GNOME Wayland session, Ubuntu
26.04):** `scrot`/`maim`/`import` grab the X11 root window, and under Wayland
that window is XWayland's empty placeholder — the capture tool exits 0 and
writes a non-empty PNG, but the image is solid black. The adapter refuses to
offer these three tools as a screenshot capability under Wayland, failing
closed (`adapter_unsupported`) instead of serving a black image as if it were
real. Which session type applies is read from the **verified graphical
session's** own environment, never from the daemon's — see the M1.2 finding
below for why that distinction is load-bearing. Neither of the two Wayland-native
alternatives worked unattended in this environment either:
`org.gnome.Shell.Screenshot.Screenshot` over D-Bus returns
`AccessDenied: Screenshot is not allowed` for a non-portal caller, and
`org.freedesktop.portal.Screenshot.Screenshot` (even with `interactive:
false`) never emits a `Response` signal — the request hangs indefinitely, and
the only log trace is a benign `Failed to associate portal window with
parent window` warning from `xdg-desktop-portal-gnome`. Installing
`gnome-screenshot` (not present by default here) is untested but is the
adapter's preferred tool and the most likely path to a working non-interactive
capture; that install was not validated in this run.

**Real-run finding (M1.2, 2026-08-04, same host): `screenshot: true` was
advertised in a Wayland session.** The guard above was correct but was asked
the wrong question. It read `XDG_SESSION_TYPE` from the **daemon's own**
environment, and a daemon started at boot by lingering has no such variable —
GNOME populates the user *manager* at login, not an already-running process. So
the Wayland check silently did not apply, `scrot` was offered because it exists
on `PATH`, and the phone showed an enabled Screenshot button. Requesting one
failed with `scrot: Can't open X display`: a bounded `adapter_failed`, no black
image and no false success, so this was an advertisement-accuracy defect rather
than a capture-correctness one. Capability is now derived from the verified
session returned by `detect_graphical_session()` — the same live source that
already decided whether GUI actions are possible at all — and a capture runs
with that session's display variables rather than the daemon's. Wayland screen
capture itself is still unavailable here; the flag now says so truthfully.

**Real-run finding (M1 Ubuntu validation, GNOME Wayland session, Ubuntu
26.04): applications reported "succeeded" but never opened.** The service runs
with `NoNewPrivileges=yes`, which drops file capabilities across `execve` for
every process it forks. Ubuntu's Firefox is a snap, and `snap-confine` needs
permitted capabilities (`cap_sys_admin`, `cap_dac_override`, …) to set
confinement up, so launching it as a child of the service failed at once with
`snap-confine is packaged without necessary permissions`. The old adapter
returned the PID without waiting, so the failure was invisible and the action
was recorded as succeeded. `xdg-open` hid it a second way: it exits 0 after
delegating whether or not the browser ever starts.

The adapter now hands each application to the **systemd user manager** as a
transient unit (`systemd-run --user`), which is not under `NoNewPrivileges`, and
confirms the launch — the process is still alive after a settle window, or an
existing instance of the same browser is visible — before reporting success.
Applications also get their own cgroup, so restarting Cofferdam no longer kills
the browser it opened. The service's hardening is unchanged.

| Symptom | Likely cause |
|---|---|
| Screenshot fails with `adapter_unsupported` | no capture tool installed (step 2), a Wayland session with only `scrot`/`maim`/`import` on PATH (they are rejected under Wayland — see above), or a Wayland session with no capture tool at all |
| `open_application` / `open_url` fail with `adapter_unsupported` and "no active graphical session" | nobody is logged in at the desktop yet — the service came up through lingering. Log in; the capability returns on the next status refresh |
| `open_application` reports "exited immediately instead of starting" | the application really did fail to start — read the detail; `journalctl --user -u 'cofferdam-app-*'` has the unit's own output |
| `open_application` says "not installed" | the browser is a snap with a different binary name — check `which firefox` |
| Phone cannot reach the host | wrong `COFFERDAM_BIND_HOST`, phone not on the tailnet, or `tailscale status` shows the host offline |
| Service dead after reboot | `loginctl enable-linger` not run |
| `/api/status` reports `"stub": true` | `COFFERDAM_ADAPTER=stub` is set — remove it; the run is not valid otherwise |
| Screenshot button enabled, then the action fails `scrot: Can't open X display` | the M1.2 over-advertisement, fixed on 2026-08-05. If it reappears, the daemon is deciding session type from its own environment again rather than from `detect_graphical_session()` |
| **Login loops back to GDM after the password is accepted** | a unit is pulling `graphical-session.target` in before login. Recover from a TTY (Ctrl+Alt+F3): `systemctl --user disable --now cofferdam-workstation.service`. This is the M1 regression — see [`SERVICE_LIFECYCLE.md`](SERVICE_LIFECYCLE.md). Do **not** delete `~/.config`, `~/.local`, `~/.cache`, or reset dconf |
| Service exits and stops after ~10 tries at boot | its bind address never appeared. `tailscale status`; check `COFFERDAM_BIND_HOST`; raise `COFFERDAM_BIND_WAIT_SECONDS` if the tailnet is slow to come up |
