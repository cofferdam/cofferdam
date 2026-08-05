# YouTube Dedicated Player

Setup and user guide for M2E — one persistent Cofferdam-owned YouTube player on
the workstation, driven from the phone.

See also: [`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md) for the YouTube
Data API key that makes search work, [`MEDIA_RESULTS.md`](MEDIA_RESULTS.md) for
how a search result becomes something openable,
[`SPOTIFY_PLAYBACK.md`](SPOTIFY_PLAYBACK.md) for the other player, and
[`AUDIO_CONTROL.md`](AUDIO_CONTROL.md) for this computer's own speaker.

---

## Why a dedicated player instead of normal watch tabs

Before this milestone, picking a YouTube search result opened
`https://www.youtube.com/watch?v=…` in a new Opera tab. That worked once. It had
three problems that got worse the more you used it:

1. **Every video was a new tab.** Ten songs meant ten tabs, and nothing ever
   closed them.
2. **Cofferdam could not control anything.** Once the tab existed, there was no
   pause, no volume, no next. The phone's job ended at "opened".
3. **A tab appearing was treated as success.** Opera launching says nothing
   about whether a video played. The old path could not tell "playing" from
   "the page loaded and sat there".

The fix is to stop trying to control a page Cofferdam does not own. Instead
Cofferdam serves **its own minimal player document**, opens it once, and talks
to it over a bounded local channel. Choosing another video calls
`loadVideoById` on the player that is already open.

**Open in YouTube has not gone away.** It is still there on every result card,
it still opens the normal watch page in Opera, and it is now an explicit choice
rather than the only behaviour. Use it when you want the real YouTube page —
comments, description, the full site.

---

## What the official API does and does not give us

Verified against the current official documentation on **2026-08-06**:

* [IFrame Player API reference](https://developers.google.com/youtube/iframe_api_reference)
* [Embedded player parameters](https://developers.google.com/youtube/player_parameters)

The findings that shaped this design:

| Question | What the documentation says | What Cofferdam does |
| --- | --- | --- |
| How is the player loaded? | `https://www.youtube.com/iframe_api`, then `onYouTubeIframeAPIReady` | Exactly that, once, on the player page |
| Changing video | `loadVideoById(...)` plays, `cueVideoById(...)` loads without playing | `loadVideoById` on the existing player — this is the whole milestone |
| Player states | `-1` unstarted, `0` ended, `1` playing, `2` paused, `3` buffering, `5` cued | Mapped to Cofferdam's own words; the numbers never reach a client |
| Next / Previous | `nextVideo()` and `previousVideo()` are documented **only** in terms of a YouTube *playlist* | **Not used.** See "The queue" below |
| Mute | Real `mute()`, `unMute()`, `isMuted()` | Used directly. This is a genuine mute |
| Volume | `setVolume(0–100)`, `getVolume()` | Used directly, and confirmed by reading back |
| Position | `getCurrentTime()`, `getDuration()` | Reported as observed, and dated |
| Video ids | No formal published grammar; every documented id is 11 URL-safe base-64 characters | Enforced as a defensive bound, re-checked at every step |
| Autoplay | `onAutoplayBlocked` fires "any time the browser blocks autoplay or scripted video playback" | Surfaced as its own state — see "Autoplay" below |
| `origin` | "you should always specify your domain as the `origin` parameter value" | Set to the player page's own loopback origin |
| Errors | `2` bad parameter, `5` HTML5 error, `100` not found/private, `101`/`150` embedding disallowed | Mapped to Cofferdam sentences; anything else becomes "no error reported" |

Nothing above is from memory. If YouTube changes any of it, the place to fix is
`cofferdam/workstation/youtubeplayer/channel.py` (state and error mapping) and
`web/player.js` (the calls themselves).

### Why the API's own queue is not used

`loadPlaylist` / `cuePlaylist` / `nextVideo` / `previousVideo` / `playVideoAt`
all operate on a **YouTube playlist**. There is no documented behaviour for
`nextVideo()` when no playlist is loaded.

Cofferdam could pass an array of video ids and get a working queue. It does not,
because doing so would hand YouTube the ordering, the advance-on-end behaviour,
and the loop/shuffle state — and the one thing this product must never do is let
a *recommendation* become the next video. A queue whose contents Cofferdam
cannot enumerate is not a queue it can honestly report.

So the queue is Cofferdam's, the player is only ever told to load **one specific
verified video id**, and Next is a Cofferdam decision that happens to be
implemented with `loadVideoById`.

---

## The one-player lifecycle

A player is **a document that is currently saying so**. Not a process, not a
window, not "Opera is running".

The player page registers with Cofferdam and then posts its observed state every
two seconds. A player that has not reported within eight seconds is gone. That
one mechanism gives both the connection state and tab-close detection, and it is
why **a running Opera is never reported as a connected player**.

### What happens when you press Play now

1. The search result is resolved to a video, server-side.
2. Cofferdam looks for a connected player.
3. If there is one, the video is loaded into it. **No tab is opened.**
4. If there is not, Opera is launched **once**, pointed at the player page.
5. Cofferdam waits up to ~24 seconds for the player to report in.
6. The original Play now then continues on its own — you do not press anything
   again.
7. The video is loaded, playback is requested, and the player's own report is
   read back.
8. The answer says what was *observed*: playing, autoplay-blocked, or partial.

Two presses that overlap do not open two tabs: the launch decision is behind a
lock, and the second press is refused as busy rather than queued.

A launch that produces no player is a **truthful timeout**, not a second launch.
Pressing Play now again is a fresh, deliberate request and may launch once more —
which is what makes "reopen the player I closed" work without any risk of a
launch loop.

### Closing the player

Close the tab whenever you like. Within a few seconds the phone shows *player
closed*, every transport control disables, and the next Play now opens a new one.
The queue survives (it lives in the daemon, not the tab); the current video does
not, because nothing is loaded anywhere.

---

## Autoplay, and the one click

Browsers refuse to start unmuted audio that the user did not ask for. This is a
browser rule and Cofferdam cannot switch it off — it is worth knowing that the
product is not doing anything wrong when it happens.

Two documented facts make it manageable:

* **Muted autoplay is always allowed**; unmuted playback needs user activation.
* Media autoplay requires **sticky activation**, which is *never consumed* and
  lasts the lifetime of the document.

That second one is why this is a small problem rather than a constant one: **one
click on the player window enables playback for the rest of the session**, not
once per video.

So the design is:

* the player page embeds its iframe with `allow="autoplay; encrypted-media; …"`,
  which is what delegates autoplay permission to the cross-origin frame — without
  that attribute, clicking the page would grant activation to the page and not to
  the player inside it;
* Cofferdam never starts muted and calls that success;
* if the browser refuses, the phone reports `autoplay_blocked`, the chosen video
  stays **loaded and cued**, and the player window shows an **Enable playback**
  button;
* one click there, and the phone works normally afterwards;
* Cofferdam sends `playVideo` **once**. There is no retry loop — the browser is
  not going to change its mind without a gesture, and hammering it would only
  bury the message that explains what to do.

### Is a first-use step required?

**Usually once, on the first player window after a fresh browser profile**, and
then not again for that window. It depends on Chromium's Media Engagement Index
for the player's origin, which Cofferdam does not read and does not try to
influence. The product does not promise it will never appear; it tells you
truthfully when it has, and makes resolving it one click.

---

## The queue

A bounded, in-memory, **Cofferdam-owned** list, with a cursor at the item
currently loaded.

| Action | What it does |
| --- | --- |
| **Play now** | Inserts after the cursor and plays it. What you were watching stays reachable with Previous. |
| **Add to queue** | Appends to the end. **Sends nothing to the player** — it cannot interrupt playback, structurally. |
| **Next** | Loads the next item. With nothing queued it **refuses** rather than playing a suggestion. |
| **Previous** | Loads the previous item. At the start it refuses. |
| **Remove** | Drops one item. Does not stop playback. |
| **Clear** | Empties the list. Does not stop playback. |

Limits and lifetime:

* **25 items.** Add to queue at capacity is refused — silently dropping one of
  your earlier choices to make room would be Cofferdam editing your list. Play
  now at capacity reclaims the oldest *already-played* entry instead, so the
  product's primary action never fails for an unrelated reason. Nothing upcoming
  is ever discarded.
* **Bounded metadata**: a truncated title, a channel, a date. Nothing else.
* **No persistence in this milestone.** The queue is empty after a service
  restart, and the phone says so rather than claiming a continuity it does not
  have. A list of what someone lined up to watch is a record of their evening,
  and holding it on disk buys nothing.
* An item stays playable even if the search that produced it has since expired:
  its authority was checked when it was added.

**Automatic continuation when a video ends is deferred.** Next is manual. The
machinery to advance automatically is small, but "should Cofferdam start
something without being asked" is a product question that deserves its own
decision rather than arriving as a side effect of a queue.

---

## Volume and mute: three separate things

This is the part most worth reading twice.

| Control | What it changes | Where |
| --- | --- | --- |
| **Computer Audio** | This machine's actual output — PipeWire/WirePlumber, the level the laptop's volume keys change | *Audio* panel |
| **Spotify** | A Spotify Connect device's own level, possibly a speaker in another room | *Spotify player* panel |
| **YouTube player** | One video player's own level, inside one browser window | *YouTube player* panel |

They are independent:

* Changing YouTube's volume **does not** change Computer Audio. The player
  package imports no audio module at all — this is structural, not a rule
  someone has to remember.
* Changing Computer Audio **does not** rewrite the YouTube player's reported
  volume. The player snapshot is built only from what the player reported.
* Muting YouTube mutes **the video**, not the machine.

Details:

* Range is **0–100**, and both ends are accepted.
* Out-of-range, negative, `NaN`, fractional and non-numeric values are
  **refused, never clamped**. A request for 150 is not a request for 100.
* After setting a level, Cofferdam **reads it back** on a bounded schedule and
  only reports success once the player agrees. A volume that did not take is
  reported as `youtube_volume_not_observed`, with the level the player actually
  reports.
* Mute uses the official `mute()`/`unMute()`. The API preserves the volume
  across them, so unmuting needs no remembered level — unlike Spotify, where
  there is no mute operation at all and Cofferdam has to say so.

---

## Privacy

What you watch is personal, and the product treats it that way.

**Shown in the authenticated PWA:** current video title, channel, position,
duration, and the queue. That is the point of the panel.

**Never written to any log or audit record:** video titles, channel names,
search queries, video ids, queue contents, player event payloads, URLs, browser
state, or tokens. The audit record for a player action carries the operation,
the outcome, a timestamp, and a random correlation id — enough to answer "did
the write path work", and nothing that would turn the action log into a viewing
history.

The loopback listener does **not** log request lines, for the same reason: in a
service journal they would become a timestamped record of when somebody was
watching something.

**No viewing history is stored anywhere.** The queue and the current video live
in memory and are gone when the service restarts.

**Cofferdam does not touch your browser or your Google account.** No cookie is
read, no browser profile is inspected, no YouTube account data is accessed, no
DOM is scraped, no screenshot or OCR is taken, and no remote-debugging port is
opened. The player page runs in Opera as an ordinary web page and can see
nothing about you that any web page could not.

---

## Security model

### The player channel

The player page is served by a **second, loopback-only listener**, separate from
the main API. It binds to `127.0.0.1` and nothing else; the address is a module
constant, so no environment variable or config key can widen it. It is never
reachable over Tailscale. It binds lazily — a host where nobody opens a player
never opens a socket.

**There is no token on the player page, in its URL, or in its storage.** A
long-lived credential sitting in a browser tab, its history, and whatever the
browser syncs is a worse thing to have than the problem it solves. Instead the
page is authenticated by *where it can reach*.

**What that boundary is worth, stated plainly:** a process running as this user
could already launch a browser, read the token file, and drive the whole API. The
loopback channel therefore grants nothing a same-user process did not already
have. What it must not do is grant anything to code that is *not* a same-user
process, which is what these four defences are for:

1. **Loopback-only bind**, as above.
2. **Host header check.** A request whose `Host` is not a loopback authority is
   refused before its body is read. This is the DNS-rebinding defence: without
   it, a web page you visit could point its own domain at `127.0.0.1`.
3. **`application/json` required** on every channel path. A cross-origin `fetch`
   with that content type is not a "simple request", so the browser must
   preflight it — and this listener answers no CORS headers to any preflight,
   ever. This is what stops a malicious page in your own browser from reaching
   the channel.
4. **No CORS headers, anywhere.** Not on success, not on error, not on
   `OPTIONS`.

Plus: fixed paths (no request path becomes a filesystem path), a 2 KB body cap,
bounded connections, a bounded long-poll, a socket timeout, and a strict CSP on
the player document that permits no inline script.

The channel vocabulary is **closed in both directions**. Cofferdam can send five
commands — `load_video`, `play`, `pause`, `set_volume`, `set_muted` — and the
player can say three things — `register`, `state`, `ack`. **A player page cannot
request any Cofferdam action.** A compromised player page gets to lie about what
is playing; it does not get a foothold.

### The API

Every route is authenticated with the device token. No `GET` mutates anything —
in particular, reading player state never opens a player.

A client may send: a search id, a result id, a queue item handle, one integer,
one boolean. There is **no field** for a YouTube URL, a watch URL, a video id, an
iframe source, a player command string, JavaScript, a browser tab id, an
executable path, a token, or a callback URL. A body carrying one is **refused by
name**, not filtered — silently dropping it would teach a client the attempt was
fine.

Video ids never travel to a client either. The PWA sees opaque handles
(`ytv-…`, `ytq-…`, `ytp-…`); the id stays server-side, which is what makes "the
client cannot submit a video id" structural rather than merely validated.

Expired and cross-provider search sessions fail closed. A Spotify result cannot
enter the YouTube player, and a YouTube result cannot enter the Spotify player.

No shell is used anywhere. Opera is launched through the existing allowlisted,
fixed-argv path — the same one the Media panel's Open button uses.

---

## API routes

All require `Authorization: Bearer <device token>`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/youtube/player` | Connection, current video, playback, volume, queue |
| `GET` | `/api/youtube/activity` | Phase of a slow operation. No network, free to poll |
| `POST` | `/api/youtube/player/open` | Open the player, if one is not open |
| `POST` | `/api/youtube/player/pause` | Pause, confirmed by observation |
| `POST` | `/api/youtube/player/resume` | Resume, confirmed by observation |
| `POST` | `/api/youtube/player/next` | Load the next **Cofferdam queue** item |
| `POST` | `/api/youtube/player/previous` | Load the previous queue item |
| `PUT` | `/api/youtube/player/volume` | `{"volume_percent": 0–100}` |
| `PUT` | `/api/youtube/player/mute` | `{"muted": true|false}` |
| `DELETE` | `/api/youtube/player/queue` | Clear the queue |
| `DELETE` | `/api/youtube/player/queue/{queue_item_id}` | Remove one item |
| `POST` | `/api/media/searches/{search_id}/results/{result_id}/youtube/play` | Play that verified result |
| `POST` | `/api/media/searches/{search_id}/results/{result_id}/youtube/queue` | Queue that verified result |

Outcomes are `applied`, `queued`, `autoplay_blocked`, or `partially_applied`.
Only the first two are recorded as successes — `partially_applied` means
something did not do what was asked, and the log should not say it did.

---

## Troubleshooting

**The player did not open.**
Check that Opera is installed and launchable from the graphical session — the
*YouTube player* panel says *unavailable on this host* if it is not, and does not
offer a button that could only fail. Otherwise look for the window on the
workstation: it may have opened behind something.

**The player opened but never registered.**
The phone reports a registration timeout after ~24 seconds. That is truthful, not
a crash — the tab may still be loading. If the window is now open, press Play now
again. Cofferdam will not open a second tab while one is connected. If it happens
every time, the loopback listener may be unreachable from the browser; check
whether anything on the machine is intercepting `127.0.0.1`.

**Autoplay blocked.**
Expected on a fresh player window. Click **Enable playback** in the player
window once; the phone works normally afterwards for the rest of that window's
life. See "Autoplay" above.

**"The video's owner does not allow it to play in an embedded player."**
YouTube error `101`/`150`. Nothing is wrong with Cofferdam and nothing will fix
it — the uploader has disabled embedding. Use **Open in YouTube**.

**"That video is unavailable."**
YouTube error `100`: removed, private, or not available in this region.

**Search returns nothing / quota errors.**
That is the catalogue, not the player. See
[`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md) — the default YouTube Data
API allocation is about 100 searches a day and resets at midnight Pacific.

**Volume "did not confirm".**
The command was delivered and the player still reports a different level. Refresh
and try again. Cofferdam deliberately does not report this as success.

**Next says there is nothing after this.**
That is correct behaviour with an empty queue. Cofferdam never picks a YouTube
recommendation for you. Add something with **Add to queue**.

**The phone shows a video that is not playing.**
The player reports; Cofferdam repeats. If they disagree with what is on screen,
press Refresh — the reading is dated ("at last check") precisely so it is never
mistaken for live.
