# Service lifecycle

How the Cofferdam workstation service relates to the GNOME/GDM login lifecycle,
why it must never own that lifecycle, and how to install, migrate, roll back,
and recover from a TTY.

This document exists because of a real regression: enabling the M1 service made
Ubuntu unable to complete a graphical login. Read [Root cause](#root-cause)
before changing anything in `deploy/`.

---

## The rule

**Cofferdam must never control the GNOME/GDM lifecycle.** It may *observe and
follow* a real graphical session. It must never create, fake, start, stop,
restart, terminate, or own one.

`Wants=` is not a wait. It is an activation request. A unit that can start
before login must never name `graphical-session.target` in `Wants=`,
`Requires=`, `Requisite=`, `BindsTo=`, `PartOf=`, or `Upholds=`.

---

## Root cause

### Directly observed facts

All from the journal of the affected machine (Ubuntu 26.04, GNOME Wayland,
NVIDIA RTX 4050), comparing a failing boot with a working one.

1. `loginctl show-user` reports `Linger=yes`.
2. On a failing boot (boot `-4`), PID 1 started the user manager before any
   login:

   ```
   06:44:45  systemd[1]: Starting user@1000.service - User Manager for UID 1000...
   06:44:45  (systemd)[2485]: pam_unix(systemd-user:session): session opened for user nrgis(uid=1000) by nrgis(uid=0)
   06:44:45  systemd[2485]: Queued start job for default target default.target.
   ```

   The PAM service is `systemd-user`, not `gdm-password`: this is lingering, not
   a login.
3. In that same manager, one second later:

   ```
   06:44:45  systemd[2485]: Reached target graphical-session.target - Current graphical user session.
   06:44:45  systemd[2485]: Started cofferdam-workstation.service - Cofferdam workstation (personal AI workstation runtime).
   ```

   `graphical-session.target` was active **23 seconds before** any
   `gdm-password` session opened, on a machine sitting at the login screen.
4. When the password was then accepted:

   ```
   06:45:08  gnome-session-i[5417]: A graphical session is already running!
   06:45:09  gdm-password][5093]: pam_unix(gdm-password:session): session closed for user nrgis
   06:45:09  gnome-shell[3184]: GdmUserVerifierProxy: connection is closed
   ```

   The same three lines repeat for every subsequent attempt, in boots `-4`,
   `-3`, `-2`, and `-1`.
5. On the working boot (boot `0`, with the unit moved aside), the same manager
   `systemd[2447]` reached `graphical-session.target` at `07:01:22` — *after*
   login, driven by gnome-session — and no Cofferdam unit appears anywhere.
6. The installed unit carried `Wants=graphical-session.target`,
   `After=graphical-session.target`, and `WantedBy=default.target`, and was
   enabled through `~/.config/systemd/user/default.target.wants/`.
7. `~/.config/autostart` was empty. The only user unit was Cofferdam's.
8. On boot `-2` the service also exited `status=3/NOTIMPLEMENTED` and was
   restarted on a 3s timer, while `tailscaled` was still reporting
   `network is unreachable` for every bootstrap address.

### Supported interpretation

Facts 1–3 establish the mechanism. Lingering starts `user@1000.service` at
boot; that manager runs `default.target`; `default.target` pulled in
`cofferdam-workstation.service`; and the service's `Wants=` pulled
`graphical-session.target` into the same transaction, activating it with no
compositor, no session, and nothing behind it.

Fact 4 is the consequence. `gnome-session` refuses to start when the target it
is itself responsible for activating is already active, because that normally
means a second session is being started on top of an existing one. It quits,
the session dies, and GDM takes the screen back — a loop that repeats on every
attempt because the boot-time activation is recreated on every boot.

Fact 5 is the control: same machine, same manager, unit disabled, target
activated only after login by gnome-session, login succeeds.

Fact 8 is a **second, independent defect** on the same unit: binding directly
to a Tailscale address that does not exist yet fails, and
`StartLimitIntervalSec=0` disabled the rate limiter, so the failure respawned
every 3 seconds indefinitely. This did not cause the login loop, and it is
fixed separately.

### Unproven assumptions

These are consistent with the evidence but were **not** independently verified,
and are recorded so they are not mistaken for established fact:

- That `gnome-session`'s refusal is *specifically* triggered by
  `graphical-session.target` being active, rather than by another piece of
  session state that Cofferdam's activation happened to bring with it. The
  correlation is exact across five boots, and the target is the only thing
  Cofferdam touched, but the gnome-session source was not read.
- That no NVIDIA, dconf, or home-permission factor contributed. Those were
  ruled out by the user's own earlier bisection (resetting only dconf did not
  help; disabling only `~/.config/systemd/user` did), not by this analysis.
- That the fix holds across repeated reboots. **Not yet observed** — see
  [Pending validation](#pending-validation).

### What would falsify this

- Enabling the corrected unit and still seeing "A graphical session is already
  running!" at login.
- `systemctl --user list-dependencies --reverse graphical-session.target`
  showing any Cofferdam unit after the migration.
- `graphical-session.target` reaching `active` in `user@1000.service` before a
  `gdm-password` session opens, with the corrected unit installed.

Any of these would mean the mechanism above is wrong or incomplete.

---

## Architecture

### One service, not two

The daemon and the GUI-launching path live in **one** unit. That was a
deliberate decision, re-examined during this fix, and it rests on one fact:

**Cofferdam does not launch graphical applications itself.** It asks the
systemd **user manager** to start each application as a transient unit
(`systemd-run --user`). The user manager is the component that actually holds
the real session — GNOME imports `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`,
`XDG_CURRENT_DESKTOP`, and `XDG_SESSION_TYPE` into it at graphical login — and
Cofferdam never owns or fakes any of it.

So a single always-on unit never has to pretend a lingering process has a
graphical session. It asks, truthfully, every time.

A split into `cofferdam-daemon.service` plus a session-scoped
`cofferdam-session-agent.service` was considered and **rejected for now**:

- it would not have prevented this regression, which was a dependency-directive
  bug, not a component-boundary bug;
- the component that would have "owned" GUI operations is still the user
  manager either way, so the agent would be an extra hop, not an extra
  guarantee;
- it adds an authenticated local IPC surface, a registration protocol, and
  capability-state synchronisation — real attack and failure surface — for no
  behaviour the current design does not already deliver.

It remains the right answer **if** Cofferdam ever needs to hold live
session-scoped resources itself (a compositor connection, a portal handle, a
persistent browser automation channel). Revisit it then, not before.

### Responsibilities

| | Owner |
|---|---|
| HTTP/API, authentication, action records, policy | Cofferdam daemon |
| Deciding whether a graphical session exists | Cofferdam daemon (read-only query) |
| Holding the real session environment | systemd user manager |
| Actually starting a browser/application | systemd user manager (transient unit) |
| Starting/stopping `graphical-session.target` | **GNOME only** |
| Session teardown at logout | **GNOME/logind only** |

---

## Lifecycle behaviour

### Boot

1. `tailscaled` starts (system unit; Cofferdam does not order against it —
   see [Tailscale](#tailscale-and-binding)).
2. Lingering starts `user@<uid>.service`, which runs `default.target`.
3. `default.target` starts `cofferdam-workstation.service`.
4. **No GNOME target is created, activated, or touched.**

### Before graphical login

- The API is reachable from the phone over Tailscale.
- `/api/status` reports `session_type` and a `capabilities` map.
- `screenshot`, `open_application`, and `open_url` are **false**.
- GUI actions are **rejected** with `adapter_unsupported` and the reason
  "no graphical session is active on this host yet".
- Nothing is ever reported as succeeded. There is no fake session and no
  silent queueing.

### During and after login

- GNOME activates `graphical-session.target` and imports the session
  environment into the user manager.
- The next `/api/status` refresh sees the target active, confirms the
  compositor socket named by `WAYLAND_DISPLAY` (or `DISPLAY`) really exists,
  and turns GUI capabilities **true**.
- Applications launch as transient `cofferdam-app-*` units of the user manager,
  in their own cgroups.

### Logout

- GNOME deactivates `graphical-session.target`.
- The daemon stays up and healthy; the API stays reachable.
- GUI capabilities go **false** on the next refresh.
- **Cofferdam does not terminate, restart, or interact with GNOME.**
- The user manager keeps stale `DISPLAY`/`WAYLAND_DISPLAY` values — under
  lingering it is never torn down — and the compositor socket may briefly still
  exist. This is why the *target state*, not the environment, decides.

### Next login

- A fresh session activates the target with a new
  `ActiveEnterTimestampMonotonic`.
- That stamp is the **session generation marker**. It is captured when a request
  is accepted and re-checked immediately before the application is launched, so
  an action authorised against one session can never be delivered into another.
- GUI capabilities return; no stale environment is reused.

### Cofferdam crash or restart

- GNOME is unaffected.
- Already-running browsers are unaffected: each lives in its own transient unit
  and cgroup, not as a child of the service.
- The unit restarts at most 10 times in 5 minutes, then stops.

---

## Tailscale and binding

The service binds **only** to `COFFERDAM_BIND_HOST`. It never binds a wildcard
address, and it never falls back to one.

At boot the daemon frequently starts before `tailscaled` has an address. Rather
than failing (and, under the old unit, respawning forever), it waits for the
address to become assignable, bounded by `COFFERDAM_BIND_WAIT_SECONDS`
(default 120), then exits cleanly with a diagnostic.

Ordering against `network-online.target` would not help: that is a *system*
unit, and naming it from a user unit is a silent no-op. The unit says so in a
comment so nobody re-adds it believing it does something.

Tailscale being unavailable is a bounded degraded state. It never becomes a
graphical-session operation.

---

## Installation

```bash
deploy/install-workstation-service.sh --dry-run   # print the plan, change nothing
deploy/install-workstation-service.sh             # install/migrate and start
```

Also enable lingering if the daemon should be reachable before login:

```bash
loginctl enable-linger $USER
```

### Lingering decision

**Lingering stays enabled, and it is safe now.** Lingering was never the bug —
it was the condition that *exposed* the bug, by starting `default.target`
before login. With the graphical dependency removed, an always-on unit under
`default.target` is exactly what lingering is for.

Lingering is a user-level setting, not a Cofferdam-owned one, so the
uninstaller leaves it alone unless `--disable-linger` is passed.

**Lingering is never treated as evidence that a graphical session exists.**

### Migration sequence

`install-workstation-service.sh` is idempotent and transactional:

1. Inventory the installed unit, enablement symlinks, linger state, and
   active/enabled state.
2. Back up Cofferdam-owned unit files and a list of enablement symlinks to
   `~/cofferdam/state/service-backups/<timestamp>/`.
3. `systemctl --user disable` — removes only symlinks systemd created for this
   unit.
4. Stop only Cofferdam-owned units. Running `cofferdam-app-*` units are the
   user's browser windows and are deliberately left alive.
5. `daemon-reload`.
6. Install the corrected unit.
7. Validate: `systemd-analyze --user verify`, plus a hard refusal if the unit
   names `graphical-session.target` in any activating directive.
8. Enable under `default.target` only.
9. Start.
10. Verify the API, and that Cofferdam does **not** appear in
    `list-dependencies --reverse graphical-session.target`.

The migration never deletes `~/.config`, `~/.local`, `~/.cache`, or dconf;
never removes unrelated user units; never changes GNOME settings; never
overwrites secrets; and never enables automatic login.

---

## Rollback and recovery

### Rollback

```bash
deploy/uninstall-workstation-service.sh
```

Or by hand:

```bash
systemctl --user disable --now cofferdam-workstation.service
rm -f ~/.config/systemd/user/cofferdam-workstation.service
systemctl --user daemon-reload
```

This removes only Cofferdam's own unit file and enablement symlinks. GNOME,
dconf, and all unrelated user configuration are untouched. The device token,
`workstation.env`, and action records are untouched.

### Recovery from a TTY

If a graphical login ever fails again, the desktop is not needed to recover.
Switch to a text console with **Ctrl+Alt+F3**, log in, and run:

```bash
systemctl --user disable --now cofferdam-workstation.service
systemctl --user daemon-reload
```

Then switch back with **Ctrl+Alt+F1** (or **F2**) and log in, or reboot.

If the user manager itself is wedged, the unit can be neutralised without
systemd at all:

```bash
mv ~/.config/systemd/user/cofferdam-workstation.service ~/cofferdam-workstation.service.disabled
rm -f ~/.config/systemd/user/default.target.wants/cofferdam-workstation.service
```

Reboot afterwards. Do **not** delete `~/.config`, `~/.local`, `~/.cache`, or
reset dconf — none of that was ever necessary, and it destroys unrelated
configuration.

---

## Automatic login

GNOME automatic login is **not** part of this change and must not be enabled
without explicit approval. The architecture supports it as a future option, but
the installer never touches it, and a static test fails if any shipped script
does.

---

## Enforcement

`tests/test_service_unit_lifecycle.py` fails if any of these reappear:

- a shipped unit names `graphical-session.target` in an activating directive;
- an always-on unit orders against it;
- a session-scoped unit is `WantedBy=default.target`;
- anything shipped starts, stops, restarts, or isolates that target;
- the session adapter uses anything but a read-only query;
- a unit pins `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY`, or the entry point
  requires them;
- a restart policy is unbounded or tighter than 1s;
- `systemctl --user exit`, `loginctl terminate-user`/`terminate-session`,
  `gnome-session-quit`, `pkill`, or `killall` appear;
- source signals a process directly (`os.kill`, `SIGKILL`, `.terminate()`);
- a unit or script embeds a secret, or configures a wildcard bind;
- an installer or uninstaller touches a user configuration tree, dconf, GNOME
  settings, or automatic login.

Run them with:

```bash
python -m unittest tests.test_service_unit_lifecycle -v
```

---

## Validation record

The root cause is verified and the logout/login cycle has been observed, but
**the regression must not be called fixed until the reboot checks below have
actually been observed.** A login loop is a boot-time failure; a successful
logout/login does not prove it is gone.

| # | Check | Status |
|---|---|---|
| 1 | Unsafe unit disabled → login succeeds | **passed** — observed, boot `0` |
| 2 | Corrected unit enabled → login succeeds, no return to GDM | **passed** — 2026-08-04 |
| 3 | Logout → daemon healthy, GNOME logout normal, not terminated by Cofferdam | **passed** — 2026-08-04 |
| 4 | Login again → capabilities return, no stale session values | **passed** — fresh session id `46292380` |
| 5 | Cofferdam absent from `graphical-session.target` reverse deps | **passed** — verified after login |
| 6 | Effective unit: `Wants=`/`PartOf=`/`BindsTo=` empty, no graphical target in `After=` | **passed** — read from systemd, not the file |
| 7 | Daemon restart → GNOME survives, browsers survive | **passed** — gnome-shell PID unchanged; Opera and Firefox both survived |
| 8 | Browser launch after login → real window, no false success | **passed** — Firefox 0→1 process, real PID, transient unit active |
| 9 | Bind wait bounded; instant when the address exists | **passed** — 3.00s on an unassigned address, 0.000s on the live one |
| 10 | **First reboot** → boots normally, login succeeds, daemon auto-starts | **passed** — boot `-1`, 2026-08-04 |
| 11 | **Second reboot** → same, no loop recurrence | **passed** — boot `0`, 2026-08-04 |
| 12 | **API before login** → reachable, GUI capabilities false, actions refused | **passed** — panel reachable, GUI reported unavailable, buttons disabled |
| 13 | Post-login capability recovery | **passed** — capabilities returned; real Firefox launch, PID 11414 |
| 14 | Bind wait exercised **in production** at boot | **passed** — see below |
| 15 | Full Tailscale outage → bounded degraded state end-to-end | pending — only the wait logic is verified, in isolation |

**The login-loop regression did not reproduce across the full validation
sequence.** Counting the failure signature per boot:

| Boots | Unit state | `A graphical session is already running!` |
|---|---|---|
| `-6`, `-5`, `-4` | old unit enabled | 2, 3, 2 |
| `-3` | disabled (control) | 0 |
| `-2`, `-1`, `0` | **corrected unit enabled** | **0, 0, 0** |

And the boot-time ordering is now inverted relative to the regression. On boot
`0`, the lingering manager `systemd[2443]` started Cofferdam **without** reaching
the graphical target, and reached it only *after* the real login:

```
19:02:50  systemd[2443]: pam_unix(systemd-user:session): session opened   ← lingering
19:02:50  systemd[2443]: Started cofferdam-workstation.service            ← no graphical target
19:03:10  gdm-password][5032]: pam_unix(gdm-password:session): session opened  ← real login
19:03:11  systemd[2443]: Reached target graphical-session.target          ← after login, by GNOME
```

Compare the failing boot, where the same manager reached the target 23s *before*
any login. This is the exact inversion the fix was meant to produce.

### The bind wait fired in production

Boot `0` is direct evidence, not a simulation — `tailscaled` had no address when
the daemon started, and the wait absorbed it:

```
19:02:50  [cofferdam] binding to 100.116.199.35 — ensure this is a private ... interface
19:02:50  [cofferdam] 100.116.199.35 is not assigned to any interface yet — waiting up to 120s
19:02:53  INFO:     Uvicorn running on http://100.116.199.35:7101
```

`NRestarts=0`. Under the old unit this was an exit and a 3s respawn loop.

Reboot, logout, and visual confirmation are **not** performed automatically.

### Known issue — screenshot capability is over-advertised after login

Found during boot-`0` validation, and **not** a false success.

This does not affect the pre-login contract: before login the graphical target
is inactive, so *every* GUI capability is correctly `false`. The problem appears
**after** login, on a daemon that was started at boot.

`linux_x11._screenshot_tool()` decides whether to reject the X11-root capture
tools by reading **its own** `os.environ["XDG_SESSION_TYPE"]`. A daemon started
at boot under lingering has an empty environment — GNOME populates the *user
manager*, not this already-running process — so once a session exists the
Wayland guard is skipped, `scrot` is offered, and `/api/status` reports
`screenshot: true` on a Wayland host where it cannot work.

Requesting one then **fails closed**, verified live:

```
status: failed
error : adapter_failed — "screenshot tool failed (scrot)"
detail: "scrot: Can't open X display. It *is* running, yeah? [NULL]"
```

So the "never report success" invariant holds, and no black frame is returned.
The defect is that the phone offers a control that will fail, which contradicts
the capability contract.

It is the same family as the login loop — trusting the daemon's frozen
environment instead of the live session — and
`linux_session.detect_graphical_session()` already does it correctly via
`user_manager_environment()`. The fix is to read the session type from the same
live source. Deliberately **not** bundled into this change, which is scoped to
the login-lifecycle regression.

Note this is why the same host reported `screenshot: false` earlier in
validation: that instance had been restarted *after* login and so inherited
`XDG_SESSION_TYPE=wayland`.

### Validation runtime (temporary)

The installed unit's `ExecStart` points at `~/cofferdam/slots/a`, which on this
host holds a *different, paused* branch. To validate this branch's Python
against a live session without disturbing that checkout, a systemd **drop-in**
redirects the runtime path only:

```
~/.config/systemd/user/cofferdam-workstation.service.d/10-pr10-validation-runtime.conf
```

It overrides `ExecStart` and `WorkingDirectory` and nothing else — `COFFERDAM_HOME`,
`EnvironmentFile`, the device token, and the state directory are all inherited
unchanged, so no secret is copied and no state is duplicated.

Remove it once this branch is merged and `slots/a` carries the code:

```bash
rm -rf ~/.config/systemd/user/cofferdam-workstation.service.d
systemctl --user daemon-reload
systemctl --user restart cofferdam-workstation.service
```

> Note: this branch is M1-based, so its browser allowlist is
> firefox/chromium/google-chrome. Opera support belongs to the separate M2A
> work and is deliberately not present here.
