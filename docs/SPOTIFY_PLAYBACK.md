# Spotify playback (M2D)

Controlling **your own Spotify account's player** from the phone: what is
playing, pause and resume, previous and next, Spotify's own volume, its Connect
devices, and playing or queueing a track you picked from a search result.

This is a different thing from two features it sits between, and the difference
is the first thing to get straight.

| | What it controls | Credential | Needs Premium |
|---|---|---|---|
| **Catalogue search** (M2B3A.1) | Nothing. Reads the public catalogue. | The *application's* client id and secret | No |
| **Spotify playback** (this document) | Your account's player and its Connect devices | Your *user* authorization | **Yes** |
| **Computer audio** (M2C) | This machine's speakers, via PipeWire | None | No |
| **YouTube player** (M2E) | One Cofferdam player window on the workstation | The catalogue API key only | No |

Three of those have a control called "volume" and they are not the same control.
Turning Spotify down does not change this computer's level, turning this computer
down does not change Spotify's, and neither of them touches the YouTube player.
The PWA keeps them in three panels with three headings for exactly that reason.
See [`AUDIO_CONTROL.md`](AUDIO_CONTROL.md) and
[`YOUTUBE_PLAYER.md`](YOUTUBE_PLAYER.md) for the other two.

---

## What the current Spotify documentation says

Verified against the official developer documentation on **2026-08-05**. Spotify
changes this material, so anything below that stops matching the official docs
should be treated as the docs being right and this file being stale.

**Authorization Code with PKCE is supported and is the right flow here.**
Spotify recommends it wherever a client secret cannot be safely stored, and it
needs **no client secret at all** for the token exchange. That is why it is used
rather than plain Authorization Code: the catalogue-search secret already on
this host never travels anywhere near the authorization path, so nothing in that
path can leak it.

**A loopback redirect URI is permitted.** HTTPS is required for redirect URIs
*unless* the address is a loopback address, where HTTP is allowed. The
documentation requires the explicit IPv4 or IPv6 form — `localhost` is **not**
accepted. Cofferdam registers exactly:

```
http://127.0.0.1:8888/callback
```

**Every player endpoint requires Spotify Premium.** Reading state and
controlling playback are both Premium-only. A free account can search the
catalogue and open Spotify; it cannot drive the player.

**A new app is in development mode, with a small user allowlist.** That allowlist
applies to *user* tokens, which is exactly what this feature mints — so the
Spotify account that completes the authorization **must be added to the app's
allowed users** or Spotify will refuse. See
[§ Development mode](#development-mode-add-yourself-to-the-allowlist).

**Rate limiting is a rolling 30-second window**, and a 429 carries a
`Retry-After` header. Cofferdam surfaces that number and never sleeps on it: a
daemon that blocked on a provider's Retry-After would have handed that provider
the ability to stall the workstation.

**Refresh tokens may or may not rotate.** The PKCE documentation states that a
refresh response "might not include a new refresh token". Cofferdam therefore
**keeps** the token it holds when a response omits one. Treating the absence as
"the token is gone" would disconnect a working account at the next restart, and
it would look like the user's fault.

**Spotify publishes no mute operation.** There is no mute endpoint anywhere in
the player API; volume is the only mechanism. See
[§ Mute is volume zero](#mute-is-volume-zero-and-says-so).

**A device id is not an identity.** The documentation describes it as "unique
and persistent to some extent" and allows it to be `null`. "To some extent" is
not an identity, so Cofferdam never treats one as stable. See
[§ Devices](#devices-and-why-a-handle-expires).

**The endpoints and methods used**, all under `https://api.spotify.com`:

| Operation | Method and path |
|---|---|
| Current playback state | `GET /v1/me/player` — `204` when nothing is playing anywhere |
| Available devices | `GET /v1/me/player/devices` |
| Start or resume playback | `PUT /v1/me/player/play` |
| Pause | `PUT /v1/me/player/pause` |
| Previous | `POST /v1/me/player/previous` |
| Next | `POST /v1/me/player/next` |
| Set volume | `PUT /v1/me/player/volume?volume_percent=…` |
| Transfer playback | `PUT /v1/me/player` |
| Add to queue | `POST /v1/me/player/queue?uri=…` |

`PUT /v1/me/player/seek` exists and was read during the investigation. It is
**not implemented** in this milestone: scrubbing needs a progress control the
panel does not have yet, and shipping the endpoint without one would be a route
with no product behind it.

The successful answer to every player write is `204 No Content`. That is Spotify
saying *"I have your request"*, not *"the speaker changed"* — and the
documentation warns that "the order of execution is not guaranteed when you use
this API with other Player API endpoints". Everything in the next section follows
from those two sentences.

---

## What real validation changed (M2D.1)

The first version of this feature was validated from a phone against a real
Premium account on 2026-08-05. Queue, next, previous and switching tracks all
worked. Three things did not, and all three had the same shape: the code looked
**once** and believed what it saw.

**Spotify closed meant "no device", full stop.** Pressing Play now with the
desktop application shut reported *"Spotify has no active device"* and gave up —
a true statement about the device list and a useless one about the product, since
Cofferdam can open Spotify itself. It now does: one launch through the same
allowlisted application launcher the Media panel uses, then a bounded wait for the
Connect device to register, then the track you asked for.

**Spotify open but idle also meant "no device".** The device was there with
`is_active` false, and that was refused too — which is exactly why *Open in
Spotify, then Play now* worked as a workaround. The workaround was the diagnosis.
An idle device is now made active first, using the documented transfer operation,
before the track starts.

**A single immediate read denied changes that had happened.** Spotify's player
endpoints are eventually consistent, so the read taken microseconds after a write
frequently still described the world before it. Setting the volume to 80%
reported *"set to 80% but the device reports 50%"*; the first Play now reported
*"playing something other than the track you chose"*. Both were wrong, and both
were wrong in the direction of denying a success. Every observation is now taken
on a bounded confirmation schedule — an immediate first read, then a fixed number
of further reads, then a truthful give-up.

There was a fourth, in the phone rather than the server: a state poll issued
*before* a write could resolve *after* it and repaint the old value over the newly
verified one. Every request that produces state now carries a monotonic
generation, and a response older than the newest one already applied is discarded.

None of this is a retry loop. Every wait is a fixed attempt count times a fixed
interval, Spotify is launched at most once per recovery, and playback is never
re-sent as a way of making it work — it is re-*read*.

### What you see while it happens

Cold-start recovery can take twenty seconds, so the panel names the step it is
on rather than spinning:

```
Opening Spotify…
Waiting for Spotify device…
Switching Spotify to that device…
Starting selected track…
Checking Spotify actually started it…
```

Each of those is written by the code that is about to do that thing, so the
sequence is a log rather than a script. If recovery stops at *Waiting for Spotify
device…*, that is where it stopped. The phone reads them from
`GET /api/spotify/activity`, which touches neither Spotify nor the filesystem —
watching a slow operation cannot make the rate limit it is already fighting any
worse.

If recovery fails, the panel says why and offers **Retry**, which sends exactly
one more attempt. Retry is offered only for refusals a second attempt could
genuinely fix — a device that had not appeared yet, a launch that did not take.
It is never offered for a Premium requirement, because retrying cannot fix a
subscription.

### When Cofferdam asks instead of choosing

If several Spotify devices are available and **none** of them is active, Play now
**asks which one** rather than picking. Choosing the first of three speakers
because it sorts first would start music in a room nobody named, and a device
list contains no evidence about which room somebody is standing in. One eligible
device is unambiguous and is used; an already-active device wins outright.

Restricted devices are not candidates for anything, including for being the
single unambiguous one.

### What recovery deliberately does not do

* It does not run a shell or build a command line — `open_application("spotify")`
  is a logical key on the existing allowlisted, fixed-argv path.
* It does not open a search page as a substitute for launching the player, and
  would not report that as recovery if it did.
* It does not extend to **Add to queue**. Launching Spotify because somebody
  queued a track would be a surprise; queueing behaves exactly as it did.
* It does not match devices by name. A device that appears after a launch is not
  trusted because it is called "Workstation".

---

## Nothing is reported that was not observed

Every action acts, then **re-reads playback state**, and reports what it saw.
The response carries `requested` and `observed` as separate keys, and an
`outcome`:

| Outcome | Meaning |
|---|---|
| `applied` | The re-read confirms what was asked for. |
| `partially_applied` | Spotify accepted it and the effect could not be confirmed. |
| `not_applied` | The re-read disagrees — the request landed and nothing moved. |
| `accepted_by_provider` | Spotify accepted it and it is not observable from playback state. Used **only** by queueing. |

Two operations cannot be fully verified, and both say so rather than pretending:

* **Add to queue.** A track added to the end of a long queue is not something a
  re-read confirms quickly, and this milestone only asks that it be added. So
  queueing reports `accepted_by_provider` and explicitly does **not** claim
  playback started. The currently playing track is expected to keep playing, and
  the message says so.
* **Next and previous.** What comes next is Spotify's choice — queue, shuffle,
  radio. The action reports the item it observed afterwards without claiming it
  is the "right" one, and says when the item did not change at all. (Pressing
  *previous* in the first seconds of a track restarts that track; that is normal,
  and it is reported as observed rather than as a failure.)

---

## One-time authorization, on the workstation

`127.0.0.1` on a phone is the phone. There is no arrangement in which a browser
on your phone can complete this flow, so Cofferdam does not pretend otherwise:
it opens the authorization page in **Opera on the workstation** and the PWA says,
in as many words, to continue there.

The alternative — binding the callback to the Tailscale address — would make the
registered loopback URI a lie and would put an authorization endpoint on a
network. It is not done, and the listener refuses to bind to anything but
loopback so it cannot be done by accident later.

### What happens when you press *Authorize on workstation*

1. The PWA asks the workstation to start an attempt. No URL is returned to the
   phone: it could only be completed on the workstation, and handing it over
   would invite a failure that looks like Cofferdam's fault.
2. Cofferdam generates a cryptographically random `state` and PKCE verifier,
   derives the S256 challenge, and builds the official authorization URL from
   constants plus those values.
3. A temporary HTTP listener binds to **`127.0.0.1:8888` and nothing else**. It
   serves exactly one path, `/callback`, and answers everything else with `404`
   without reading a query string.
4. The authorization page opens in Opera on the workstation.
5. You sign in and approve. Spotify redirects to the loopback URI.
6. Cofferdam compares the returned `state` against the live attempt in constant
   time, exchanges the code for tokens **once**, and stores the refresh token.
7. The listener stops — on success, on failure, or on timeout, whichever comes
   first.

The attempt expires on its own after five minutes. If you are away from the
workstation and cannot finish, press **Cancel**, or simply leave it: the panel
returns to *Spotify account not connected* and says the attempt was not
completed in time. Nothing is changed.

The page the browser shows says only that Spotify is connected and that the tab
can be closed. It carries no token, no code, and no account name — that tab is
the least private surface in the flow, it stays in Opera's history, and a
screenshot of it should be worth nothing.

### The scopes requested, and why only these

| Scope | What it is for |
|---|---|
| `user-read-playback-state` | Playback state, and the Connect devices list |
| `user-read-currently-playing` | The currently playing item |
| `user-modify-playback-state` | Pause, resume, next, previous, volume, queue, transfer |

Deliberately **not** requested: `streaming` (that is the Web Playback SDK, which
Cofferdam does not use), `user-read-email`, and `user-read-private`. The last one
would report the account's `product` tier — the documented way to read a
subscription level — but that field is marked deprecated in the current
documentation, and asking for a subscription-details scope to read a deprecated
field is a poor trade for a fact the player endpoints report anyway.

If you decline any of them, Cofferdam reports `missing_required_scopes` and names
the one that is missing, rather than failing later with something vague.

### Development mode: add yourself to the allowlist

A Spotify app starts in **development mode**, which limits it to a small
allowlist of users; the dashboard shows the current limit, and Spotify has
changed it before, so this document does not restate a number. Catalogue search
is unaffected — a client-credentials token carries no user — but **playback
authorization is a user token, so the Spotify account you authorize with must be
on that list**.

If it is not, Spotify refuses, and Cofferdam reports `provider_rejected` naming
both documented causes rather than guessing between them.

To add yourself:

1. Sign in at <https://developer.spotify.com/dashboard>.
2. Open your Cofferdam app.
3. Open **User Management** (in newer dashboards, under **Settings**).
4. Add the **full name and email address of the Spotify account** you will
   authorize with — the account whose music you want to control, which is not
   necessarily the account that owns the developer app.
5. Save, then retry the authorization.

This is the single most common reason a first authorization attempt is refused.

### Verify the redirect URI in the dashboard

1. Open your app at <https://developer.spotify.com/dashboard>.
2. Choose **Settings** → **Edit**.
3. Under **Redirect URIs**, confirm this exact value is present:

   ```
   http://127.0.0.1:8888/callback
   ```

   Exactly that — not `localhost`, not `https`, not a trailing slash, not a
   different port. Spotify matches the URI literally.
4. Save.

---

## Where the authorization is stored

```
$COFFERDAM_HOME/secrets/spotify_user_oauth.json   mode 0600, in a 0700 directory
```

Kept separate from `media_providers.json` on purpose. That file holds an
*application* credential that says nothing about any person; this one holds proof
that a human authorized Cofferdam to control their playback. Two different blast
radii, so two files: deleting this one disconnects an account, deleting that one
turns off search.

**What is stored:** the refresh token, the granted scopes, the expiry Spotify
reported, when the connection was made, and — only because the profile endpoint
volunteers it without any extra scope — a bounded display name.

**What is not stored:** the access token. It lives in memory, expires in about an
hour, and writing it to disk would put a second credential on the filesystem for
no gain. Also absent: your email, your country, any profile blob, and any
listening history.

The file is written **atomically** — created via `mkstemp` (which is `0600` from
the first byte), written, `fsync`ed, then `os.replace`d into position — so a
reader sees either the old file or the new one and never a half-written one, and
the token is never momentarily world-readable. The mode is verified after the
write rather than assumed from the umask.

### Check the permissions yourself

```bash
ls -l "${COFFERDAM_HOME:-$HOME/.local/share/cofferdam}/secrets/"
```

You want `drwx------` on the directory and `-rw-------` on
`spotify_user_oauth.json`. If the file is looser than that, Cofferdam shows a
warning in the panel telling you to fix it — and the warning names the file, not
its contents.

Never `cat` this file, never paste it into a chat message (including to an AI
assistant), and never commit it. It is excluded from Git.

---

## Disconnecting, and revoking

**Disconnect** in the PWA removes the local authorization: the token file is
unlinked, and the mute restore state goes with it. It is honest about what it
did — it says it removed the authorization stored on this machine, and that it
**did not revoke Cofferdam's access in your Spotify account**.

That distinction matters, and it is not a limitation Cofferdam chose. Spotify
publishes no revocation endpoint for this flow, so claiming revocation would
leave you believing an app no longer has access while the grant is still listed
in your account.

To revoke it properly, at Spotify:

1. Sign in at <https://www.spotify.com/account/apps/>.
2. Find the Cofferdam app in the list.
3. Choose **Remove access**.

Doing both is the complete answer: Disconnect removes the credential from this
machine, and Remove access ends the grant at Spotify.

---

## Devices, and why a handle expires

Spotify's device id is documented as persistent only "to some extent", and it may
be `null`. So the PWA never sees one. Each device gets an opaque, host-scoped
`resource_id` (`spdev-…`); the provider id stays server-side and is never sent to
a client, never written to an audit record, and never accepted from a request.

Before any device-targeted action the server **re-reads the device list**,
resolves the handle against it, and checks the device is still there and still
controllable. A handle that no longer resolves is **refused** — there is no
fallback to matching a device by *name*, because two speakers can share a name
and a name is something you typed into a phone once.

A device with a `null` id is dropped rather than published: it cannot be
addressed, and offering it would put a button in the UI that could never work.

Device handles are **not** persisted as preferences in this milestone, precisely
because they are not stable enough to be one.

**Restricted devices.** A device reporting `is_restricted` is documented as
accepting *no* Web API commands at all. That is a flat refusal, not a
degradation, so it gates every targeted action and the PWA disables the controls
rather than offering buttons that can only fail. Car head units are the common
example.

**Transferring playback** moves where *Spotify* plays. It does **not** change
this computer's audio output — that is the Computer Audio panel, a different
subsystem with a different backend — and the response says so explicitly.

---

## Mute is volume zero, and says so

Spotify has no mute. So Cofferdam's mute:

1. remembers the device's current non-zero volume,
2. sets the Spotify volume to `0`,
3. reports the state as **`muted_by_cofferdam`** — never `muted`, so no client
   can render it as a Spotify feature.

**Unmute** restores the level that was remembered. When there is no remembered
level — a fresh install, a cleared state directory, a device muted from the
Spotify app itself, or a restart that lost the record — unmute **refuses and
asks you to choose a volume**. Restoring to 50% "because that is reasonable"
would be Cofferdam deciding how loud your speakers get.

The restore level lives in:

```
$COFFERDAM_HOME/state/spotify_mute.json
```

Deliberately *not* in the OAuth secret file, which holds a credential and should
contain nothing that changes during ordinary use. The record is per device — one
speaker being muted says nothing about another — and bounded to sixteen devices.

If someone turns the volume back up from the Spotify app itself, the stale record
is dropped, so a later unmute cannot restore a level from a mute you already
undid.

A device reporting `supports_volume: false` shows volume and mute as
**unavailable**, with its own reason, rather than a greyed-out slider.

---

## Playing a track you picked

The Play now and Add to queue buttons appear on **track** results only, in
Spotify search results, and they reuse the *existing* verified search sessions —
this milestone adds no second catalogue search.

The client sends the search id and the result id the server issued, **and nothing
else**. There is no request field for a Spotify URI, a track id, or a URL, so
there is nothing to validate: the server looks up the private `ProviderItem` it
remembered for that result and rebuilds the `spotify:track:…` URI itself. A
result from another provider cannot be routed through this path, and an expired
search session refuses with "search again" rather than an internal error.

Album, artist, playlist, show and episode results keep **Open in Spotify** and
nothing more. Those are *contexts* in Spotify's model — a different endpoint with
different semantics — and inventing "play this artist" here would be inventing a
behaviour nothing has verified.

**Play now never happens automatically.** Running a search does not start
anything. A track plays because you pressed a button naming it.

When there is no active Spotify device, Play now returns a truthful
`no_active_device` state, keeps Open in Spotify available, offers the device list
so you can choose one — and does not claim playback started.

---

## Privacy

What you listen to, when, and how often is a detailed picture of a person.

* The **authenticated PWA** shows the current track, artist, album and progress.
  That is the point of the panel.
* **Audit records carry the operation and the outcome, and nothing else.** No
  track, artist, album or query; no account name; no Spotify device id; no
  volume. "A track was skipped at 21:04 and it worked" is enough to audit the
  write path, and it is the most that can be recorded without describing
  somebody's evening.
* **Nothing is written to the daemon log.** The whole `spotifyplayer` package
  makes no logging call and no `print`, and the loopback callback listener
  silences the default HTTP access log — whose request line would otherwise
  contain the authorization code.
* **No listening history is stored.** Cofferdam keeps no record of what played.
* `spotify.js` in the PWA makes no `console` call at all. A browser console is a
  surface neither of us controls.

---

## API

Every route requires Cofferdam authentication. No `GET` changes anything. Write
routes take a strict, bounded, JSON-only body, and there is no field anywhere for
a Spotify URI, a track id, a provider device id, an access token, an
authorization code, or a redirect URI.

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/spotify/playback` | — (`?refresh=true` to bypass the short cache) |
| `GET` | `/api/spotify/activity` | — (the current operation's phase; no provider call) |
| `POST` | `/api/spotify/authorize` | `{}` |
| `DELETE` | `/api/spotify/authorize` | — |
| `POST` | `/api/spotify/disconnect` | `{}` |
| `POST` | `/api/spotify/player/{pause\|resume\|next\|previous}` | `{}` |
| `PUT` | `/api/spotify/player/volume` | `{"volume_percent": 0–100, "device_resource_id"?}` |
| `PUT` | `/api/spotify/player/mute` | `{"muted": bool, "device_resource_id"?}` |
| `PUT` | `/api/spotify/player/device` | `{"device_resource_id": "spdev-…", "play"?: bool}` |
| `POST` | `/api/media/searches/{search_id}/results/{result_id}/spotify/play` | `{"device_resource_id"?}` |
| `POST` | `/api/media/searches/{search_id}/results/{result_id}/spotify/queue` | `{"device_resource_id"?}` |

The OAuth callback listener is **not** part of this application. It is a separate
loopback-only trust boundary that exists for a few minutes during authorization
and is unreachable from the tailnet.

### Connection states

| State | What it means | What to do |
|---|---|---|
| `disconnected` | No stored authorization. | Authorize on the workstation. |
| `authorization_pending` | An attempt is in flight. | Finish it in Opera, or cancel. |
| `connected` | Working. | — |
| `missing_required_scopes` | A permission was declined. | Reconnect and accept all of them. |
| `refresh_failed` | Spotify rejected the stored authorization. | Reconnect; it may have been revoked. |
| `provider_rejected` | Spotify refused without saying why. | Check Premium, and the development-mode allowlist. |
| `temporarily_unavailable` | Rate limited, or Spotify unreachable. | Wait and refresh. |
| `premium_required` | Spotify said the account is not Premium. | Playback needs Premium; search does not. |

### Refusal codes

`spotify_not_connected`, `spotify_missing_scopes`, `spotify_premium_required`,
`spotify_no_active_device`, `spotify_device_unknown`, `spotify_device_restricted`,
`spotify_volume_unsupported`, `spotify_volume_invalid`,
`spotify_unmute_restore_unknown`, `spotify_result_not_playable`,
`spotify_rate_limited`, `spotify_provider_rejected`,
`spotify_provider_unavailable`.

Added by cold-start recovery (M2D.1): `spotify_no_device_after_launch`,
`spotify_device_ambiguous`, `spotify_launch_failed`,
`spotify_playback_not_observed`.

Volume is refused, never clamped: `-1`, `101`, `12.5`, `NaN` and `"50"` are all
rejected rather than silently turned into something you did not ask for.

---

## Troubleshooting

### "Spotify has no active device"

Spotify needs somewhere to send audio, and right now there is nowhere.

Since M2D.1 you should rarely see this from **Play now** — that path opens the
desktop application for you. It still appears for pause, resume, next, previous,
volume and mute, which act on something already playing and have nothing to act
on. It is an ordinary situation, not a fault.

Open Spotify anywhere — the desktop app on this workstation, your phone, a
speaker, the web player — and *play something for a moment*. That registers it as
a Connect device. Then press **Refresh** in the panel, or pick it from the device
list and choose **Move playback here**.

A device that has been idle for a long time can disappear from the list; opening
Spotify on it brings it back.

### "Spotify was opened but no playback device appeared"

Cofferdam launched the desktop application and waited, and Spotify never
registered a Connect device. Check the workstation: Spotify may still be starting
on a cold cache, or it may be sitting on a sign-in screen. Press **Retry** once it
is up.

### "Several Spotify devices are available and none is active"

Cofferdam will not choose between them. Pick one in the **Spotify Connect
devices** list and press **Move playback here**, then Play now — or press Play now
after selecting the device in the picker.

### The authorization page opened but nothing happened

Check, in this order:

1. **Is the Spotify account on the app's allowlist?** See
   [§ Development mode](#development-mode-add-yourself-to-the-allowlist). This is
   the most common cause.
2. **Is the redirect URI exactly `http://127.0.0.1:8888/callback`?** See
   [§ Verify the redirect URI](#verify-the-redirect-uri-in-the-dashboard).
3. **Did you complete it in Opera on the workstation?** A browser on the phone
   cannot finish this, and the loopback address there points at the phone.
4. **Is something else already listening on port 8888?** `ss -ltnp | grep 8888`
   will say. The attempt fails cleanly if the port is taken.

### "Spotify refused that request"

Two documented causes, and Cofferdam will not guess between them:

* the account is not **Premium** — every player endpoint requires it; or
* the app is in **development mode** and this Spotify user is not on its
  allowlist.

### "Spotify is rate limiting Cofferdam"

Spotify limits requests over a rolling 30-second window. The panel polls
conservatively and stops entirely while the page is hidden, so this normally
resolves on its own within a minute. Nothing retries automatically.

### The volume slider is missing or the mute button is unavailable

The active Spotify device reports `supports_volume: false`. Some Connect devices
— particularly speakers with their own hardware controls — do not accept remote
volume. Use the device's own controls, or transfer playback to a device that
does.

### "Cofferdam does not know what volume to restore"

You pressed Unmute on a device Cofferdam did not mute — or Cofferdam did mute it,
but the record was lost to a restart or a cleared state directory. Set a volume
directly with the slider. Cofferdam will not pick a level for you.

### The Spotify volume moved but the room did not get quieter

Check which device is active in the **Spotify Connect devices** list. Spotify's
volume applies to the device Spotify is playing on, which may not be this
computer. If Spotify is playing on this workstation and the room is still loud,
the level you want is in the **Audio** panel — that is this machine's own output.

### Catalogue search stopped working after connecting

It should not, and the two are independent. Search uses the application
credential in `media_providers.json`; playback uses the user authorization in
`spotify_user_oauth.json`. If search is failing, see
[`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md) — disconnecting playback
will not fix it and is not the cause.

---

## Related

* [`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md) — the Spotify and YouTube
  **catalogue credential** setup (M2B3A.1). Required before this feature can be
  authorized, because PKCE still needs the application's client id.
* [`MEDIA_RESULTS.md`](MEDIA_RESULTS.md) — structured search, the search-session
  model, and the verified-result handles this feature plays from.
* [`AUDIO_CONTROL.md`](AUDIO_CONTROL.md) — this computer's volume, mute and
  output selection. A separate subsystem and a separate panel.
* [`MEDIA_PROFILES.md`](MEDIA_PROFILES.md) — which services Cofferdam can open,
  and how.
