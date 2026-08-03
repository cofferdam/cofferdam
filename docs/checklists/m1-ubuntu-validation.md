# M1 Ubuntu validation checklist

Run this on the real Ubuntu Desktop host. **M1 is not complete until every step
passes on real hardware.** Nothing here may be satisfied by the stub adapter.

> **No stubs during this run.** `COFFERDAM_ADAPTER` must be unset or `auto`, and
> `/api/status` must report `"adapter": "linux-x11"` with `"stub": false`. If
> either is untrue, stop — the run does not count.

Record the result of every step. **Write down failures rather than working
around them**: the platform constraints discovered here are the real output of
this milestone, and they feed the M2 window/display work.

| # | Step | Expected | Result |
|---|---|---|---|
| 1 | Install repository dependencies (`docs/host-setup.md` §2, §4) | `pip install -e ".[workstation]"` succeeds | |
| 2 | Install and authenticate Tailscale; note `tailscale ip -4` | host appears in the tailnet; phone can ping it | |
| 3 | Identify the session type: `echo $XDG_SESSION_TYPE` | `x11` or `wayland` — both are supported for opening applications and URLs; record which one, since screen capture depends on it | |
| 4 | Confirm the graphical session stays available after logout/idle | session persists; `loginctl` shows it active | |
| 5 | Confirm the host does not sleep (leave idle 30+ min, then reconnect) | still reachable from the phone | |
| 6 | Start Cofferdam manually (`python -m cofferdam.workstation`) | starts; token printed on first run only | |
| 7 | Connect from the phone through Tailscale | PWA loads over the tailnet | |
| 8 | Verify token authentication (wrong token, then correct) | wrong → rejected; correct → dashboard | |
| 9 | Verify live host status | cards populate; connection dot shows **live**; values update | |
| 10 | Request a screenshot | real desktop image appears on the phone within a few seconds | |
| 11 | Open Firefox (or Chromium) | browser window appears on the host | |
| 12 | Open a supplied URL | the URL loads on the host | |
| 13 | Reboot Ubuntu (`sudo reboot`) — do not touch keyboard/monitor afterwards | host comes back and auto-logs-in | |
| 14 | Confirm Cofferdam starts automatically | `systemctl --user status cofferdam-workstation` active; no manual start | |
| 15 | Repeat steps 9–12 from the phone after the reboot | status, screenshot, open app, open URL all work | |
| 16 | Record all failures and platform-specific constraints | written into `docs/host-setup.md` | |

## Additional checks worth doing while you are there

- [ ] `journalctl --user -u cofferdam-workstation` contains **no token** and no secrets.
- [ ] `ls -l ~/cofferdam/secrets/token` shows `-rw-------` (0600).
- [ ] `ss -tlnp | grep 7101` shows the Tailscale address only — **not** `0.0.0.0`.
- [ ] From a device *outside* the tailnet, the host is unreachable on 7101.
- [ ] Kill the service (`systemctl --user stop`) → the phone shows "reconnecting…"; start it again → the phone recovers on its own without a reload.
- [ ] Lock the screen, then request a screenshot — record what happens (expected: a lock-screen image or a failure; either way, note it).
- [ ] Multi-monitor: note how many displays `xrandr --listmonitors` reports and whether `display_count` matches. Screenshot content across two displays is M2 work — just record the behaviour.
- [ ] Try a snap-packaged Firefox if that is what is installed, and record whether `open_application` finds it.

## Sign-off

M1 is complete when steps 1–16 pass on the real host **and** the observed
behaviour is written back into `docs/host-setup.md`.

- Date run:
- Ubuntu version:
- Session type:
- Displays:
- Result:
