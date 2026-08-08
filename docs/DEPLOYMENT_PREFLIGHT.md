# Deployment preflight and unattended-reboot recovery

The question this document answers: **if the machine reboots and nobody logs
into the desktop, can the phone still reach Cofferdam?**

Run the read-only check first:

```bash
bash deploy/preflight.sh
```

It inspects and reports. It starts nothing, enables nothing and removes nothing
— every fix it finds is printed for a person to run deliberately. A test asserts
it stays that way, because a preflight that repairs what it finds is one nobody
can run to learn the truth.

## The four things unattended recovery depends on

### 1. Linger

```bash
loginctl show-user "$USER" -p Linger
```

Without `Linger=yes` the user manager (`user@<uid>.service`) only starts at an
interactive login, so a rebooted machine sits with no Cofferdam until somebody
walks to it and signs in. That is the difference between "the daemon restarts"
and "the daemon is reachable from a phone".

Enable it deliberately, never as a side effect of something else:

```bash
loginctl enable-linger "$USER"
```

**What that changes:** the user manager starts at boot and keeps running after
the last session ends, so `default.target` — and everything wanted by it,
including Cofferdam — comes up without a login. It does not grant privileges,
open a network surface or change authentication.

**Rollback:**

```bash
loginctl disable-linger "$USER"
```

After that, Cofferdam only runs while somebody is logged in. Nothing else about
the installation changes.

### 2. The unit is enabled and not tied to the desktop

`WantedBy=default.target`, and no `Wants=`/`Requires=`/`BindsTo=`/`PartOf=`
naming a graphical target. The M1.1 login-loop regression came from exactly that
mistake and `tests/test_service_unit_lifecycle.py` guards it.

### 3. It runs the code you think it runs

This is the one that bit us. Every milestone from M2A onward validated its work
with a drop-in:

```
~/.config/systemd/user/cofferdam-workstation.service.d/<n>-<milestone>-validation.conf
```

each overriding `ExecStart` and `WorkingDirectory` to that milestone's feature
worktree. None were removed. Twelve had accumulated by M2H, and because systemd
applies drop-ins in lexical order the highest number wins — so production had
been running M2G-era code out of `clones/claude-code-adapter` since PR #21, with
a validation-only task adapter still enabled.

Nothing caught it: every test asserted the *shipped* unit in `deploy/`, which was
always correct. The drift lived only in the installed drop-in directory.

Check what will actually run:

```bash
systemctl --user show cofferdam-workstation.service -p ExecStart -p WorkingDirectory
ls ~/.config/systemd/user/cofferdam-workstation.service.d/
```

`ExecStart` must name `~/cofferdam/slots/<a|b>`. If it names `clones/` or
`worktrees/`, production is pinned to a development checkout — a directory nobody
promised to keep, whose deletion leaves the daemon failing at every boot with
nothing obviously wrong.

**Clearing stale validation drop-ins** (back them up first; they are the only
record of how a past milestone was validated):

```bash
mkdir -p ~/cofferdam/state/service-backups/dropins-$(date +%Y%m%d-%H%M%S)
cp ~/.config/systemd/user/cofferdam-workstation.service.d/*.conf \
   ~/cofferdam/state/service-backups/dropins-*/
```

Then remove the ones that override `ExecStart`/`WorkingDirectory`, and:

```bash
systemctl --user daemon-reload
systemctl --user restart cofferdam-workstation.service
```

**Rollback:** copy the backed-up `.conf` files back, `daemon-reload`, restart.

### 4. The private address exists before it is needed

Cofferdam binds a Tailscale address and **never falls back** to another
interface — a private service that cannot reach its private interface stays down
rather than moving somewhere public. `tailscaled` is ordered after
`network-pre.target` and wanted by `multi-user.target`, so it comes up at boot;
but a *user* unit cannot order itself against a system unit reliably, so the
daemon solves the race in-process instead:

`wait_for_bind_address()` polls for the configured address for up to 120s
(`COFFERDAM_BIND_WAIT`), logs one bounded line saying it is waiting, and only
then gives up. A slow `tailscaled` therefore costs one long start rather than a
burst of failed ones, which is what keeps the unit clear of its start limit.

Observed on a real boot: machine up at `17:54:16`, user manager and service at
`17:54:24`, address not yet assigned, bound and serving at `17:54:41` — a 17
second wait, absorbed silently.

## Remote Control after a reboot

A project's Remote Control host is **not** started by a reboot, deliberately.
`deploy/cofferdam-rc@.service` ships with no `[Install]` section, so there is no
way to enable one by accident and no possibility of every registered project
acquiring a session host at boot. The property that matters is that a person can
*start* one from the phone once Cofferdam is back.

Nor does rebooting revoke anything. The native session URL is scoped to the
Anthropic **environment**, not to a launch: stopping the local host removes the
link from Cofferdam, and does not revoke a URL already shared elsewhere.
Cofferdam has no account-level revocation mechanism.

## If the phone cannot reach Cofferdam after a reboot

Physical access is the recovery path; nothing here justifies exposing the
service publicly, binding to `0.0.0.0`, or weakening authentication. Locally:

```bash
systemctl is-active tailscaled.service
loginctl show-user "$USER" -p Linger
systemctl --user status cofferdam-workstation.service
journalctl --user -u cofferdam-workstation.service -b --no-pager | tail -40
```

Read the journal for the two failure shapes that matter: a bind-wait that timed
out (the address never appeared), and a start-limit trap (`start request
repeated too quickly`). The first is a Tailscale problem, the second means
something is failing fast and repeatedly — fix the cause rather than raising the
limit.

Apply the smallest evidence-backed correction, then re-run the preflight before
trusting another reboot.
