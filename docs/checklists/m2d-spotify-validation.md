# M2D — Spotify playback: live validation checklist

The validation runtime for `feat/spotify-playback-oauth` (PR #18) is **applied**:
the `90-spotify-playback-validation` drop-in is in place and the service runs from
this clone. Round 1 was run from the phone on 2026-08-05 and found three
reliability defects; round 2, after the M2D.1 fixes, is outstanding.

Two reasons the steps are not automated. Authorizing Spotify means signing into a
real account in a real browser, and pressing play changes what is coming out of
the speakers next to the person reading this. Both are the user's to do.

---

## Pre-state, recorded 2026-08-05 23:02 +03

The runtime this rolls back to, captured before the `90` drop-in was applied.

```
ActiveState        active
MainPID            31137
NRestarts          0
WorkingDirectory   /home/nrgis/cofferdam/clones/audio-control-foundation
ExecStart          .../clones/audio-control-foundation/.venv/bin/python -m cofferdam.workstation
```

Drop-in checksums (SHA-256), all eight unmodified:

```
d7626800cd5390777b34b18e73a8e62b3c9d1aea1d7f8969c74d638c7ea9c1c8  10-pr10-validation-runtime.conf
cb112798973dc2ec81294e6511a40182e387a3be3989367225d58d7973357da9  20-pr12-screenshot-validation.conf
2fd8255804a718f1509597f58ba3ccff0e8e6dfc3c03a55de2e0ae94c3a7800e  30-pr9-m2a-validation.conf
fa800e8d9daeed853e384bfddf87e9b3db521ce4cfece8461e500285681274d4  40-pr13-runtime-inventory-validation.conf
2b19384bcbf3b3a5487ea9ba572dbc457d9211144d4a361c0dca01a30eec8076  50-m2b2-display-overlays-validation.conf
851fcc2804d171d0cdd171934aced81d578ba9776235a7c29f354ff3be050313  60-m2b3a-media-launch-validation.conf
1987eda96ce70d87bb1021f1792ef6562acfe5bbb0d5e1e9c9ffb154312bcf62  70-m2b3a1-media-results-validation.conf
6fdb071d6e29a577a6512fceb1f02c82d14041d31172aa6dd9a2806db02c8f8d  80-audio-control-validation.conf
```

## Configuration status, without any secret value

```
spotify.client_id configured        yes
spotify.client_secret configured    yes   (present, and unused by this feature — PKCE needs no secret)
spotify_user_oauth.json present     yes   (round 1 authorized a real account; mode 0600)
secrets directory mode              0700
127.0.0.1:8888                      free
redirect URI the code will use      http://127.0.0.1:8888/callback
callback bind host                  127.0.0.1
scopes requested                    user-read-playback-state user-read-currently-playing user-modify-playback-state
```

No value from `media_providers.json` was read into any output, log, or message.

## Round 2 (M2D.1) — what the first validation found

The first run happened on 2026-08-05 from the phone. Steps 1–7 passed;
authorization completed in Opera, the panel connected without showing a token,
and catalogue search, queue, next, previous and switching tracks all worked.
**Three reliability defects were found**, diagnosed and fixed — see
[`../SPOTIFY_PLAYBACK.md`](../SPOTIFY_PLAYBACK.md#what-real-validation-changed-m2d1):

1. Play now with Spotify closed reported "no active device" and stopped.
2. The volume displayed one operation behind (50 → 80 → 70 showed 50 → 80).
3. The first Play now often did not start the chosen track; repeating it, or
   using Open in Spotify first, eventually worked.

The steps below must be re-run in full. Steps 19–24 are new and cover the fixes
directly.

## Gates, all met before staging

- [x] Implementation complete
- [x] Full suite green — 1,641 tests, workstation path
- [x] Stdlib-only path green — 1,641 tests, 271 skipped
- [x] CI green on PR #18 — Trust Core stdlib-only, Workstation, license-scan
- [x] Runtime dependencies installed in the clone's venv (`fastapi`, `uvicorn`, `psutil` import; `create_app` imports)
- [x] Callback binding verified as loopback-only, and the redirect URI matches what Spotify must have registered
- [x] No secret printed, committed, logged, or returned

## Before applying: check the redirect URI in the Spotify dashboard

The one thing that cannot be checked from this machine. At
<https://developer.spotify.com/dashboard> → the Cofferdam app → **Settings** →
**Edit** → **Redirect URIs**, confirm this exact value is present:

```
http://127.0.0.1:8888/callback
```

And under **User Management**, confirm the Spotify account that will authorize is
on the app's allowed-users list. Development mode's allowlist applies to user
tokens, which is exactly what this flow mints, and this is the most common reason
a first authorization is refused. See
[`../SPOTIFY_PLAYBACK.md`](../SPOTIFY_PLAYBACK.md).

---

## Apply (already done for round 1)

Copies the staged drop-in into place. It overrides `ExecStart` and
`WorkingDirectory` only; `COFFERDAM_HOME`, the device token, the live registries
and the bind address are all inherited from the `10`–`80` drop-ins, which are not
edited.

```bash
cp ~/cofferdam/clones/spotify-playback-oauth/deploy/validation/90-spotify-playback-validation.conf \
   ~/.config/systemd/user/cofferdam-workstation.service.d/ && \
systemctl --user daemon-reload && \
systemctl --user restart cofferdam-workstation.service && \
systemctl --user show cofferdam-workstation.service -p ActiveState -p MainPID -p NRestarts -p WorkingDirectory
```

## Pick up new code on the same runtime

The drop-in points at this working tree, so a restart is all a code change needs.
No drop-in is touched.

```bash
systemctl --user restart cofferdam-workstation.service && systemctl --user show cofferdam-workstation.service -p ActiveState -p MainPID -p NRestarts -p WorkingDirectory
```

## Roll back

Removes exactly one file and returns to the unchanged `80` runtime.

```bash
rm ~/.config/systemd/user/cofferdam-workstation.service.d/90-spotify-playback-validation.conf && \
systemctl --user daemon-reload && \
systemctl --user restart cofferdam-workstation.service && \
systemctl --user show cofferdam-workstation.service -p WorkingDirectory
```

`WorkingDirectory` should read `…/clones/audio-control-foundation` again.

---

## Validation steps

Read-only up to step 7. From step 8 onward each step changes what is playing on
a real account, so each is the user's to run.

**Before authorizing**

1. Catalogue search still works — search Spotify and YouTube from the phone and
   confirm five selectable results from each.
2. The Spotify player panel reads *Spotify account not connected* and offers
   **Authorize on workstation**.
3. Track cards show *Play now* and *Add to queue* **disabled**, with the line
   explaining where to connect, and *Open in Spotify* still working.

**Authorizing**

4. Press **Authorize on workstation** from the phone.
5. Confirm Opera opens on the workstation at `accounts.spotify.com`.
6. Complete the authorization in Opera.
7. Confirm the panel becomes connected, shows no token anywhere, and reports the
   current playback and device state.

**Playback** — each of these changes the room

8. Search `Neşet Ertaş Gönül Dağı`, pick a verified track, press **Play now**;
   confirm that exact track starts.
9. Add a different verified track to the queue; confirm the currently playing
   track did **not** change.
10. Pause, then resume.
11. Previous, then next.
12. Set the Spotify volume to 25%, then to 70%.
13. Mute, then unmute; confirm the level returns to what it was.
14. Confirm the **Audio** panel's volume is a separate control and still works.
15. List Spotify Connect devices; transfer playback to another one if a second
    device is available. **If only one device exists, record this as
    hardware/device unavailable — not as a pass.**
16. Confirm no claim was made about the computer's audio output changing.
17. Confirm Spotify and YouTube result selection still open correctly.
18. Confirm no duplicate actions and no horizontal overflow on the phone.

**The M2D.1 fixes** — each one is a defect the first run found

19. **Fully close Spotify.** Search for a track and press **Play now** once.
    Confirm Spotify opens by itself, that the panel names the steps (*Opening
    Spotify… / Waiting for Spotify device… / Starting selected track…*), and that
    the exact track you chose begins **without a second tap**.
20. Set the Spotify volume to **50**, then **80**, then **70**. Confirm the phone
    ends on a verified **70** and never sits one value behind.
21. Search for another track and press **Play now** once. Confirm it replaces the
    current track immediately, with no Open in Spotify first.
22. Queue a different track. Confirm it does **not** interrupt what is playing,
    then press **next** and confirm the queued track is what arrives.
23. Re-check pause/resume, previous/next and mute/unmute.
24. Confirm the **Audio** panel's volume is still an independent control.

Nothing here should be run automatically. Until every step above has a real
result recorded against it, M2D is **not validated** and must not be described
as such.
