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
Steps 13–15 were the release gate; they were closed by the M1.1 run — see the note under the table.

| 13 | Reboot Ubuntu (`sudo reboot`) — do not touch keyboard/monitor afterwards | host comes back; if automatic login is enabled it reaches the desktop by itself, otherwise it stops at the login screen — record which | |
| 14 | Confirm Cofferdam starts automatically | `systemctl --user is-active cofferdam-workstation` reports `active` with no manual start; `ss -tlnp \| grep 7101` shows the Tailscale address; the journal shows no `cannot assign requested address` restart loop | |
| 15 | From the phone, before logging in at the desktop | status loads and authenticates; `open_application`/`open_url` report **false** and fail closed with `adapter_unsupported`; then log in at the desktop and confirm both flip to **true** and work | |
| 16 | Record all failures and platform-specific constraints | written into `docs/host-setup.md` | |

> **Gate status: steps 13–15 were closed by the M1.1 run below.** Steps 1–12 passed on
> 2026-08-03 (Ubuntu 26.04, GNOME/Wayland) inside a single continuously logged-in session, at
> which point the reboot was deferred at the user's request. It is no longer deferred: the
> M1.1 service-lifecycle validation on 2026-08-04 covered the same ground and more — **two
> consecutive reboots** (L6), automatic start after reboot, and the pre-login check from the
> phone with GUI capabilities correctly `false` and actions refused (L7). Read L1–L10 below as
> the authoritative result for steps 13–15.
>
> Automatic login is **not** enabled on this host (`/etc/gdm3/custom.conf` has no
> `AutomaticLoginEnable`), so a reboot stops at the login screen and the API returns on its own
> with GUI capabilities reporting unavailable until someone logs in. That expectation is now
> tested, not assumed — see L7.
>
> Step 10 (screenshot) passed on 2026-08-03 but its **capability reporting** was later found
> untruthful on Wayland and was corrected in M1.2; see
> [`../SERVICE_LIFECYCLE.md`](../SERVICE_LIFECYCLE.md). Screen capture itself remains
> unavailable under Wayland on this host, and `screenshot: false` is the truthful answer.

## Service lifecycle (M1.1 — added after the login-loop regression)

Enabling the M1 service made GNOME unable to complete a login. These steps exist
so that can never be signed off unnoticed again. **Do not treat the regression as
fixed until L1–L6 have actually been observed** — one successful login is not
enough. Background: [`../SERVICE_LIFECYCLE.md`](../SERVICE_LIFECYCLE.md).

> **Before you start, know the way out.** If a login ever loops back to GDM,
> switch to a text console with **Ctrl+Alt+F3**, log in, and run
> `systemctl --user disable --now cofferdam-workstation.service`. Never delete
> `~/.config`, `~/.local`, `~/.cache`, or reset dconf — none of that is needed.

| # | Step | Expected | Result |
|---|---|---|---|
| L1 | With the service **disabled**, log in | login succeeds | **passed** |
| L2 | Install via `deploy/install-workstation-service.sh`, then log out and back in | first login succeeds; **no** return to GDM | **passed** |
| L3 | `systemctl --user list-dependencies --reverse graphical-session.target` | **no** Cofferdam unit appears | **passed** |
| L4 | Log out | daemon still active; GUI capabilities `false`; GNOME logs out normally and is **not** terminated by Cofferdam | **passed** |
| L5 | Log in again | capabilities return; `open_url` works; no stale session values | **passed** |
| L6 | Reboot **twice**, logging in each time | boots normally both times; no loop; daemon auto-starts | **passed** (two reboots) |
| L7 | Before logging in after a reboot, check `/api/status` from the phone | reachable; `screenshot`/`open_application`/`open_url` all `false`; GUI actions rejected, never reported as succeeded | **passed** |
| L8 | `systemctl --user restart cofferdam-workstation` while a browser is open | GNOME survives; the **browser stays open** (it has its own cgroup) | **passed** (Opera+Firefox survived) |
| L9 | `sudo tailscale down`, wait, then `sudo tailscale up` | bounded degraded state; no restart storm in `journalctl --user -u cofferdam-workstation`; no GNOME interaction; recovers | partial — wait logic only |
| L10 | Check the boot journal for `A graphical session is already running!` | **absent** | **passed** (0 occurrences) |

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
behaviour is written back into `docs/host-setup.md`. Steps 1–12 and 16 are done, and steps 13–15
were closed by the M1.1 L-series (two reboots, automatic start, pre-login capability check).
L9 — a full Tailscale outage end to end — remains **partial**, so record that limitation rather
than describing this milestone as exhaustively validated.

- Date run:
- Ubuntu version:
- Session type:
- Displays:
- Result:
