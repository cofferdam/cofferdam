# Audio control (M2C)

Reading and changing this workstation's real audio state from the phone: which
speaker is in use, how loud it is, whether it is muted, and what is currently
making sound.

## What this controls, and what it does not

**This is the system output volume** — the same level the laptop's own volume
keys change, and the same number the desktop's volume slider shows. Turning it
down turns everything down.

It is deliberately *not* a player volume. Spotify's own playback level and a
YouTube player's own level are properties of those applications, they belong to
later milestones, and mixing them into this panel would produce two sliders that
both say "volume" and mean different things. Which one is at fault when the room
goes quiet is exactly the question a control panel should never make you ask.

| Control | Owner | Milestone |
| --- | --- | --- |
| System / output volume, mute, which speaker | this document | M2C |
| Spotify playback volume, play/pause, track | [Spotify Playback](SPOTIFY_PLAYBACK.md) | M2D |
| YouTube player volume, play/pause, queue | [YouTube Dedicated Player](YOUTUBE_PLAYER.md) | M2E |

Nothing here starts, stops, or skips playback. Choosing an output and setting a
level is the whole surface.

There are now three controls in the product called "volume", and they are three
different things: this computer's speaker, a Spotify Connect device, and one
YouTube player window on the workstation. Changing any one of them leaves the
other two alone. The PWA keeps them in three panels with three headings for
exactly that reason.

## Where the numbers come from

The backend is PipeWire with WirePlumber, read through `pw-dump` and driven
through `wpctl`. `pactl` is not used and is not required.

### The volume scale, which has a trap in it

PipeWire stores a sink's gain as a **linear** multiplier. `wpctl` — and the
GNOME slider — work in a **cubic perceptual** scale, where the stored linear
gain is the cube of the displayed value. On the development host the built-in
speaker read `0.846138` linearly and `0.95` through `wpctl`, and
∛0.846138 ≈ 0.9458, which rounds to the 0.95 `wpctl` printed.

Publishing the linear number as a percentage would put **85%** on the phone for
a speaker the laptop screen calls **95%**. So Cofferdam reads and writes volume
through `wpctl` only, on one scale, and assumes no curve anywhere. `wpctl`
prints two decimal places, which is exactly the 1% granularity of the 0–100
range, so a whole percentage round-trips without loss.

Mute is read from the graph instead, where it is an unambiguous boolean. Reading
it there rather than from the tool that set it is what makes the verification
after a write independent of the write.

## Identity, and why an output can stop existing

A PipeWire node id is a small integer the daemon hands out and **reuses after
the object it named is destroyed**. Node 58 is the built-in speaker today; after
the audio server restarts it may be a Bluetooth headset. Anything that treats
that integer as a name is one graph change away from acting on the wrong device.

So Cofferdam addresses an output by a `resource_id` derived from the host, the
audio graph, and the sink's stable node name, and it publishes:

| Field | Meaning | Survives |
| --- | --- | --- |
| `resource_id` | what an action names | this audio graph |
| `stable_id` | what a saved preference would use | reboots |
| `node_id` | PipeWire's current integer, shown as an observation | nothing |
| `object_serial` | PipeWire's monotonic counter, never reused in a graph | this audio graph |

**Practical consequence:** if the audio server restarts while the phone has the
page open, every output the page is holding stops resolving and actions are
refused with "the audio server restarted". Refreshing fixes it. This is
deliberate — the alternative is acting on whatever now occupies that slot.

Before every action Cofferdam re-reads the graph and confirms the node still
carries the same name *and* the same serial. A node id being present is not
enough; it must still be the same object.

## Choosing an output

### What "default output" actually means

Selecting an output sets the system default. **That governs where new sound
goes.** Whether audio that is *already playing* follows is a WirePlumber policy
decision, not Cofferdam's:

* a stream that connected to "the default" generally **does** follow, because
  WirePlumber's `linking.follow-default-target` is on by default;
* a stream pinned to a specific device **does not**.

Cofferdam does not assert either. It records where every stream was before the
switch, reads where each one is afterwards, and reports what it observed:

* **applied** — the default changed, and nothing was left behind.
* **partially applied** — the default changed and something already playing
  stayed where it was. The panel says so: *"new sound will now play through this
  output, but audio that was already playing stayed where it was."*
* **not applied** — the audio server did not switch.

If music does not follow, pausing and playing it again will usually pick up the
new default.

### Moving one playing stream is not offered

`move_audio_stream` is published as **unavailable**, with its reason, rather
than implemented. Two things rule it out on this host:

1. `wpctl` has no command for it. Its verbs are `set-default`, `set-volume`,
   `set-mute`, `set-profile` and `set-route`.
2. Doing it by hand means writing PipeWire metadata keyed by the stream's
   **transient node id** — the identity this codebase refuses to act on — and
   WirePlumber's `node.stream.restore-target` would then *remember* that target
   and pin the application to that output for future sessions. That is a lasting
   change nobody asked for.

A shell command accepting two numbers is not evidence that an operation is safe.

### What can and cannot appear as an output

Only sinks that exist in the running graph are listed, because only those can be
selected.

* **Built-in speakers** appear whenever the internal card has a HiFi profile
  active.
* **Headphones** on an internal analog card are usually a *route* on the same
  device, not a separate output. The device category stays "built-in" and the
  `route` field says "Headphones"; the panel shows both.
* **HDMI / DisplayPort** audio only exists when the card's profile is on, which
  normally requires something connected. A monitor plugged in for video that
  carries no audio, or a card sitting at profile `off`, publishes **no sink at
  all**. Cofferdam does not manufacture a placeholder for it — it would not be
  selectable — but it does raise a warning naming the card and saying why, so an
  absent monitor explains itself instead of looking like a bug.
* **Bluetooth** speakers appear once they are connected and PipeWire has created
  a sink for them. Pairing and connecting are done in the desktop's own
  Bluetooth settings; Cofferdam does not pair devices.
* **USB** audio appears once the device enumerates.

Switching a card's *profile* — turning an HDMI output on, or moving an internal
card to its headphone profile — is a more invasive action than choosing a
default and is not in this milestone.

## Streams: what is making sound

Where it can be observed safely, the panel lists active playback streams. This
section is collapsed, and everything else works without it.

**An application is named only on evidence it did not supply.** `application.name`
is a string a client chooses; anything can call itself Spotify. The trustworthy
field is `pipewire.sec.pid`, which the PipeWire daemon writes from the peer
credentials of the socket connection and a client cannot forge. Cofferdam
resolves that pid through `/proc` to a real executable and requires an **exact**
basename match against the application table. `spotifyd` is not `spotify`.

Anything that fails at any link stays **unclassified**, with the reason shown. A
declared name is still displayed, marked *(unverified)*, because it is what a
person recognises — but it never decides the association. Telling someone
Spotify is playing when it is not is worse than saying "unidentified".

## Privacy

**What is playing is never read.** A stream's `media.name` property holds the
track, video or page title. Published stream fields are built from an
allowlist — a named list of keys to include — rather than by copying the
property bag and removing the bad ones, because a denylist is one application
release away from leaking something nobody wrote down.

Never published, by construction:

* track, video, or browser tab titles
* URLs and cookies
* raw command lines or environment variables
* arbitrary PipeWire property dictionaries

The audit log records the operation, the resource id, the observed outcome, and
the coarse device category. It deliberately omits volume levels: knowing a
change happened and whether it worked is what makes the path auditable, while a
timestamped record of exactly how loud someone had their speakers all evening is
a more personal trace than it first looks.

## API

All routes require the device token. Reads are `GET` and change nothing; every
change is a `PUT` with a JSON body.

| Route | Purpose |
| --- | --- |
| `GET /api/audio` | the whole snapshot |
| `GET /api/audio/outputs` | outputs, with the snapshot header |
| `GET /api/audio/streams` | streams, with the snapshot header |
| `PUT /api/audio/outputs/{resource_id}/default` | choose the default output |
| `PUT /api/audio/outputs/{resource_id}/volume` | `{"volume_percent": 0–100}` |
| `PUT /api/audio/outputs/{resource_id}/mute` | `{"muted": true \| false}` |
| `PUT /api/audio/streams/{resource_id}/output` | always `501` on this host |

A client may send **a resource id in the path, an integer, and a boolean**.
There is no field for a node id, a device name, a PipeWire property, a profile,
a command, or a program — those are absent from the schemas rather than
validated and rejected, and unknown fields are refused rather than ignored. No
shell is ever constructed; every backend invocation is a fixed argument vector
built from constants plus a node id the backend derived itself.

Volume is refused, never clamped: `150` returns `422`, because a client asking
for 150 has a bug and quietly giving it 100 hides that bug. Values above 100%
are not offered at all — on this hardware amplification distorts rather than
getting louder.

Every action response separates what was asked from what was seen:

```json
{
  "operation": "set_output_volume",
  "outcome": "not_applied",
  "requested": { "volume_percent": 25 },
  "observed":  { "volume_percent": 50 },
  "message": "the volume was set to 25% but this output reports 50%"
}
```

`wpctl` exits zero for a command it *accepted*. Accepted is not applied, so the
outcome is computed by comparing observed state against the request. The
requested value is never the evidence for success.

### Collection status

Each collection reports its own status, and an empty `ok` is not the same
statement as `unavailable`:

* `ok` — the list is complete. Zero outputs with `ok` means there genuinely are
  none.
* `partial` — real items, something missed; `warnings` says what.
* `unavailable` — nothing on this host can answer. There are no items, and this
  is *not* "nothing is playing".
* `error` — a backend that should have worked failed.

## Troubleshooting

**An output I expect is not listed.** Check the snapshot's warnings — a card at
profile `off` names itself there. For HDMI, the usual cause is that the display
is connected for video but the audio card has no active profile; open the
desktop's Sound settings and confirm the device appears there. If it does not
appear there either, it is not a Cofferdam problem.

**"That output is not available on this machine right now."** The id the page
was holding no longer resolves — the device was unplugged, or the audio server
restarted. Refresh.

**"The audio server restarted while the request was being handled."** Exactly
what it says; refresh and try again. Nothing was changed.

**The volume on the phone disagrees with the laptop.** They should match to the
nearest percent; both use the perceptual scale. A persistent disagreement means
something else changed the level between the read and the look.

**Mute does nothing.** Check the outcome message. If it reports `not_applied`,
the audio server accepted the command and did not apply it, which usually means
the sink is being reconfigured; refresh and retry.

**Streams are unavailable.** Volume, mute and output selection are unaffected.
Cofferdam degrades to not claiming anything about what is playing.

**Nothing at all is readable.** Confirm the services are up:

```bash
systemctl --user is-active pipewire wireplumber
```

## Related

* [`RUNTIME_INVENTORY.md`](RUNTIME_INVENTORY.md) — the runtime resource model
  this reuses: identities, collection statuses, and the `unavailable`-is-not-
  `empty` rule. A stream's `process_resource_id` is the same identity the
  process inventory publishes.
* [`MEDIA_RESULTS.md`](MEDIA_RESULTS.md) — finding and opening something to
  play, which is what produces the streams this panel observes.
* [`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md) — Spotify and YouTube
  credential setup.
* [`SPOTIFY_PLAYBACK.md`](SPOTIFY_PLAYBACK.md) — **Spotify's own player volume
  and Connect devices, which are not this panel.** Turning Spotify down does
  not change this machine's output level, and turning this machine down does
  not change Spotify's. Two controls, two panels, two subsystems.
