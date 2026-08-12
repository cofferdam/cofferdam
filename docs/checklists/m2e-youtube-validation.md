# M2E validation checklist — YouTube dedicated player

> ## Read this before running anything (2026-08-12)
>
> **Do not apply the `95-youtube-player-validation` drop-in.** Section 0's pre-state, section 1's
> install, and section 3's rollback all describe a **pre-merge runtime** that pointed the live
> service at an unmerged feature clone. That runtime no longer exists. Production runs the
> **merged A/B slot deployment** (slot `a`, plus adapter drop-ins only), and this feature is part
> of it. Re-applying the drop-in would recreate the production-drift class that M2H PR4 removed
> and `test_deployment_drift.py` guards.
>
> **Status (D-2026-08-12-1): the section 2 walkthrough is `DEFERRED_NON_BLOCKING` — not executed,
> and not a pass.** It does not block M2J PR1. The endpoint exists in the merged build and reports
> `disconnected` / empty queue / `idle` truthfully when no player window is open.
>
> One check in section 0 is also stale: `ss -ltnp 'src 127.0.0.1' | grep -c python` no longer
> returns `0` before the first player opens, because the Actions bridge — which did not exist when
> this was written — is itself a loopback Python listener. Read the player's own state from
> `/api/youtube/player` instead.
>
> If the walkthrough is run later, run it against the **merged production build**, not against a
> clone. The steps themselves remain the acceptance intent and are unchanged.

Live validation of `feat/youtube-dedicated-player` on the real workstation, run
under an isolated validation runtime that can be removed in one command.

**Do not start this until** the implementation is complete, the full suite and
both CI paths are green, and the runtime dependencies are installed in the
clone's virtualenv. Nothing here is automatic: each numbered step is something a
person does and observes.

---

## 0. Pre-state — record this before touching anything

Recorded **2026-08-06**, before any change:

| Fact | Value |
| --- | --- |
| `ActiveState` / `SubState` | `active` / `running` |
| `MainPID` | `79268` |
| `NRestarts` | `0` |
| `ExecMainStartTimestamp` | `Wed 2026-08-05 23:58:08 +03` |
| Effective `ExecStart` | `/home/nrgis/cofferdam/clones/spotify-playback-oauth/.venv/bin/python -m cofferdam.workstation` |
| Effective `WorkingDirectory` | `/home/nrgis/cofferdam/clones/spotify-playback-oauth` |
| Highest drop-in in effect | `90-spotify-playback-validation.conf` |

Drop-in checksums (`sha256`) — these must be **unchanged** at the end:

```
d7626800cd5390777b34b18e73a8e62b3c9d1aea1d7f8969c74d638c7ea9c1c8  10-pr10-validation-runtime.conf
cb112798973dc2ec81294e6511a40182e387a3be3989367225d58d7973357da9  20-pr12-screenshot-validation.conf
2fd8255804a718f1509597f58ba3ccff0e8e6dfc3c03a55de2e0ae94c3a7800e  30-pr9-m2a-validation.conf
fa800e8d9daeed853e384bfddf87e9b3db521ce4cfece8461e500285681274d4  40-pr13-runtime-inventory-validation.conf
2b19384bcbf3b3a5487ea9ba572dbc457d9211144d4a361c0dca01a30eec8076  50-m2b2-display-overlays-validation.conf
851fcc2804d171d0cdd171934aced81d578ba9776235a7c29f354ff3be050313  60-m2b3a-media-launch-validation.conf
1987eda96ce70d87bb1021f1792ef6562acfe5bbb0d5e1e9c9ffb154312bcf62  70-m2b3a1-media-results-validation.conf
6fdb071d6e29a577a6512fceb1f02c82d14041d31172aa6dd9a2806db02c8f8d  80-audio-control-validation.conf
88f11c965371357271fdc6e46d6078b95a8837d1cab2b3034bd362eaadeabf10  90-spotify-playback-validation.conf
```

Re-record them yourself before installing:

```bash
sha256sum ~/.config/systemd/user/cofferdam-workstation.service.d/*.conf
```

### Player port state, without secrets

The player's loopback listener binds **lazily** — before the first player is
opened there is nothing to see, which is itself worth confirming:

```bash
ss -ltnp 'src 127.0.0.1' | grep -c python
```

There is no port to configure and no secret associated with it. The port is
ephemeral, chosen by the kernel, never written to a file, and never sent to the
phone.

### The exact rollback command

```bash
rm ~/.config/systemd/user/cofferdam-workstation.service.d/95-youtube-player-validation.conf && systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

That removes **only** the M2E layer and returns the service to the unchanged
`90-spotify-playback` runtime. No other drop-in is touched, and there is no
separate player unit to stop — the loopback listener lives inside this daemon
and goes with it.

---

## 1. Install the validation layer

```bash
cp ~/cofferdam/clones/youtube-dedicated-player/deploy/validation/95-youtube-player-validation.conf ~/.config/systemd/user/cofferdam-workstation.service.d/
```

```bash
systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

### Confirm the layer actually took effect

**Do not skip this.** The number in the filename is load-bearing and the obvious
choice is wrong.

systemd applies drop-ins in **byte-wise** lexicographic order of their filenames.
A file called `100-…` sorts immediately after `10-…` and *before* `20-…` … `90-…`,
so it would be overridden by the M2D drop-in and the service would go on running
the old clone with no error anywhere. That is not a hypothetical: a `100-` file
was installed during this milestone, reloaded and restarted, and left
`WorkingDirectory` pointing at the M2D clone. `systemctl --user cat` showed why.

`95-` sorts after `90-` byte-wise and is unambiguous. (`systemd-analyze
compare-versions` reports `90-… < 100-…`, but that command implements *version*
comparison and is not what orders drop-in filenames. The manual page's word
"lexicographic" is literal.)

Whatever the number, confirm the result rather than the name:

```bash
systemctl --user show cofferdam-workstation.service -p ExecStart -p WorkingDirectory
```

`WorkingDirectory` must now read
`/home/nrgis/cofferdam/clones/youtube-dedicated-player`. If it still reads
`spotify-playback-oauth`, the layer did not win — roll back and stop.

To see the order systemd actually used:

```bash
systemctl --user cat cofferdam-workstation.service | grep -E '^# /home.*\.conf$'
```

The M2E file must be **last** in that list.

Then confirm the service is healthy and has not restarted in a loop:

```bash
systemctl --user show cofferdam-workstation.service -p MainPID -p NRestarts -p ActiveState
```

`NRestarts` should be `0` for the new start. A climbing count is a login-loop
regression: roll back immediately.

---

## 2. Real validation

Run these in order, from the phone unless stated. **Do not change what is
playing without asking first** if anything else is using the speakers.

| # | Step | Expected |
| --- | --- | --- |
| 1 | Search YouTube for something real | Five selectable results, as before |
| 2 | Confirm no Cofferdam player window is open on the workstation | *YouTube player* panel says **player closed** |
| 3 | Press **Play now** on one result, **once** | — |
| 4 | Watch the workstation | Opera opens **exactly one** Cofferdam player window |
| 5 | Observe the outcome | The video plays, **or** the phone says autoplay blocked and the video is loaded and cued |
| 6 | If blocked: click **Enable playback** in the player window, once | Playback starts; the phone works normally afterwards |
| 7 | Pick a different result and press **Play now** | — |
| 8 | Watch the workstation | The **same** window changes video. **No second tab.** |
| 9 | **Add to queue** on two different videos | Queue count becomes 2 |
| 10 | Observe playback while queueing | Nothing was interrupted; the current video keeps playing |
| 11 | Press **Next**, then **Previous** | Moves through the Cofferdam queue, in the same window |
| 12 | Press **Pause**, then **Play** | Both observed and reported; state matches the window |
| 13 | Set YouTube volume 25, then 80, then 70 | Each confirmed |
| 14 | Read the phone at the end | Shows a verified **70** |
| 15 | **Mute**, then **Unmute** | The video goes silent and comes back at 70 |
| 16 | Check the *Audio* panel and the desktop volume | **Unchanged** throughout steps 13–15 |
| 17 | Close the Cofferdam player window | — |
| 18 | Watch the phone for a few seconds | Flips to **player closed**; transport controls disable |
| 19 | Press **Play now** once | **One** bounded relaunch; the video plays |
| 20 | Use the *Spotify player* panel: play, pause, volume | Still fully functional |
| 21 | Press **Open in YouTube** on a result | The normal watch page opens in Opera, as before |
| 22 | Check phone and tablet layout | No horizontal scrolling; a double tap sends one request |

### What counts as a failure

* A second Opera tab at step 8 or 19.
* The phone reporting "playing" while the window shows something else.
* The phone ending on anything other than 70 at step 14.
* Computer Audio changing at step 16.
* `NRestarts` climbing at any point.
* Any video title, channel or search query appearing in
  `journalctl --user -u cofferdam-workstation.service`.

Check that last one explicitly:

```bash
journalctl --user -u cofferdam-workstation.service --since "1 hour ago" | grep -iE "watch\?v=|videoId|title" | head
```

Expected output: nothing.

---

## 3. Rollback

Whether validation passes or fails, the rollback is the same single command:

```bash
rm ~/.config/systemd/user/cofferdam-workstation.service.d/95-youtube-player-validation.conf && systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

Then confirm the pre-state is restored:

```bash
systemctl --user show cofferdam-workstation.service -p ExecStart -p WorkingDirectory -p NRestarts
```

`WorkingDirectory` must read `/home/nrgis/cofferdam/clones/spotify-playback-oauth`
again, and the drop-in checksums in section 0 must be unchanged:

```bash
sha256sum ~/.config/systemd/user/cofferdam-workstation.service.d/*.conf
```
