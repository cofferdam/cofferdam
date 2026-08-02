# Ubuntu host setup (M1)

How to turn an Ubuntu Desktop machine into the always-on Cofferdam host and
reach it from a phone. **None of this has been executed on a real Ubuntu host
yet** — it is the plan to follow during the M1 validation run
([`checklists/m1-ubuntu-validation.md`](checklists/m1-ubuntu-validation.md)).
Correct this file with whatever the real run teaches.

## 0. Choose the session type — X11, not Wayland

At the Ubuntu login screen, click the gear icon and pick **"Ubuntu on Xorg"**.

Under GNOME's Wayland session, cross-application screenshots and window control
are restricted by design, which breaks screenshots now and window placement in
M2. The service reports `session_type` in `/api/status`, and the UI shows a
warning when it sees `wayland`, so a mistake here is visible rather than silent.

Confirm:

```bash
echo $XDG_SESSION_TYPE   # expect: x11
```

## 1. Keep the host awake

An always-on host must not suspend, and the graphical session must stay logged
in (the service needs it to control the desktop).

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.session idle-delay 0
```

Enable automatic login (Settings → Users → Automatic Login) so the graphical
session comes back by itself after a reboot.

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

```bash
mkdir -p ~/.config/systemd/user
cp ~/cofferdam/slots/a/deploy/cofferdam-workstation.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cofferdam-workstation
loginctl enable-linger $USER     # survive logout/reboot

systemctl --user status cofferdam-workstation
journalctl --user -u cofferdam-workstation -f
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

| Symptom | Likely cause |
|---|---|
| Screenshot fails with `adapter_unsupported` | no capture tool installed (step 2), or a Wayland session (step 0) |
| `open_application` says "not installed" | the browser is a snap with a different binary name — check `which firefox` |
| Phone cannot reach the host | wrong `COFFERDAM_BIND_HOST`, phone not on the tailnet, or `tailscale status` shows the host offline |
| Service dead after reboot | `loginctl enable-linger` not run, or automatic login disabled |
| `/api/status` reports `"stub": true` | `COFFERDAM_ADAPTER=stub` is set — remove it; the run is not valid otherwise |
