# Device and display registries

Two of the six registries described in [`CONTROL_PLANE.md`](CONTROL_PLANE.md). Both are **user
overlays** — optional names, aliases, and metadata. Neither can say how to reach something or
what to do to it.

Files (optional; absent on a fresh install):

```
$COFFERDAM_HOME/config/registries/devices.json
$COFFERDAM_HOME/config/registries/displays.json
```

[`examples/registries/`](../examples/registries/) holds *format illustrations* — every id and
name there is prefixed `example`, and nothing copies them into `$COFFERDAM_HOME`. The real files
are machine-specific and are never committed.

> ## These are not a live inventory
>
> **A display entry is a label waiting for a display. It is not a connected display.**
>
> Cofferdam performs **no runtime discovery in M2A**. Writing `displays.json` does not make a
> display exist, and reading it tells you nothing about what is plugged in right now. Discovering
> connected displays is the next milestone (*M2B — Runtime inventory*, see
> [`../ROADMAP.md`](../ROADMAP.md)).
>
> The intended order is **discover first, label second**: the live system reports a display, and
> that display may *then* be given an optional label such as "Büyük monitör". Nothing ships
> pre-named — there is no `large-monitor`, "main monitor", "small monitor", or "laptop display"
> anywhere in this repository or in a default installation, because inventing a name before the
> resource exists is a guess that will quietly disagree with the machine.
>
> A device entry is likewise something **you declared**, not something Cofferdam found.

---

## Common rules

Every registry file uses one envelope:

```json
{ "version": 1, "items": [] }
```

- Unknown top-level fields fail validation. Only `version` and `items` are accepted.
- An unknown `version` **fails closed** with a structured error. A newer file may mean something
  this build cannot honour, and guessing is how a permission boundary quietly widens.
- A missing file is a valid, empty, version-1 registry. "Not configured" is a normal state.
- A malformed file is reported, **never** rewritten. Overwriting it would destroy the only record
  of what the user meant.
- Readers never return partially validated data: a registry loads completely, or not at all.

Every item carries:

| field | rule |
| --- | --- |
| `id` | matches `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`, ≤64 chars, unique in its registry, compared **exactly**, immutable once referenced |
| `name` | human-readable, any Unicode, ≤120 chars |
| `aliases` | list of human-readable strings, ≤24 entries |
| `enabled` | boolean |

Unknown item fields fail validation. Version 1 has **no** forward-compatibility escape hatch:
adding a field means bumping the version and documenting the migration. A code-owned denylist
(`command`, `argv`, `executable`, `path`, `script`, `env`, `user_data_dir`, `token`, `cookies`,
`prompt`, `selector`, `tab_id`, `conversation_id`, …) is refused by name so the error explains
the boundary rather than saying "unknown field".

### Alias normalization

Applied to the **comparison key only**; the original text is always kept for display.

1. Unicode NFC, so composed and decomposed spellings of `ö` agree.
2. Trim leading/trailing whitespace, collapse repeated internal whitespace.
3. Unicode case folding (`str.casefold`) — not ASCII lowering.
4. NFC again, because folding can decompose.
5. Fold Turkish dotted and dotless I onto `i`.

Step 5 exists because Unicode's language-neutral folding is deliberately reversible:
`"İ".casefold()` is `i` + COMBINING DOT ABOVE, not `i`, and `"ı"` folds to itself. Without the
tailoring, "MONİTÖR" would not match "monitör" and "IŞIK" would not match "ışık" — exactly the
phrases this product exists to understand. The cost is that two aliases differing only by ı/i
collide; that is a **validation failure**, not a silent conflation.

IDs stay ASCII. Aliases may contain Turkish characters — or any others.

### Ambiguity is refused, never resolved

Both `name` and every entry of `aliases` are indexed. Duplicate normalized phrases inside one
registry fail validation, and the resolver itself returns *no* match when a phrase maps to more
than one item. Alias resolution never silently chooses between multiple matches.

Resolution order is: exact `id` first, then the normalized phrase index. A precise reference
cannot be hijacked by someone naming one display after another display's ID.

---

## `devices.json`

A device is a machine **you declare**. Cofferdam does not go looking for machines.

```json
{
  "id": "example-workstation",
  "name": "Example workstation",
  "aliases": ["örnek bilgisayar"],
  "enabled": true,
  "kind": "workstation",
  "platform": "linux",
  "notes": null
}
```

| field | allowed values |
| --- | --- |
| `kind` | `workstation`, `raspberry-pi`, `phone`, `tablet`, `other` |
| `platform` | `linux`, `windows`, `macos`, `ios`, `android`, `other` |
| `notes` | optional plain text, ≤500 characters, or `null` |

**This is descriptive only.** M2A stores no network credentials, IP addresses, hostnames,
commands, executables, SSH configuration, or power-control configuration — and has no field that
could hold them. A `raspberry-pi` entry names a future node; it does not enable Raspberry Pi
control, Wake-on-LAN, or any power action, none of which M2A implements.

`notes` is bounded plain text for a human reader. **It is never interpreted as instructions** —
not by Cofferdam, and not by being handed to a model as though it were part of a prompt.

## `displays.json`

**An optional label, not a connected display.** On a machine that has not been configured, this
file is absent and that is correct. "Which displays are attached?" is answered by the runtime
inventory (`GET /api/runtime/displays`), never by this file; an entry here is a name waiting for a
panel that discovery may or may not find.

```json
{
  "id": "example-display",
  "device_id": "example-workstation",
  "name": "Example display",
  "aliases": ["örnek ekran"],
  "enabled": true,
  "match": {
    "connector_hint": null,
    "manufacturer": null,
    "model": null,
    "serial": null,
    "edid_sha256": null
  }
}
```

- `device_id` must reference an existing device — **enabled or disabled**. A display attached to a
  machine that is currently switched off is still a real display; only a *missing* device is a
  configuration error. Dangling references fail validation.
- `match` is optional; when present, all five fields are optional strings or `null`, and no other
  field is accepted.
- `connector_hint` is exactly that — a hint. `DP-1` becomes `DP-2` when a cable moves, so it may
  help find a panel but is never treated as permanent identity.
- `edid_sha256`, when present, must be exactly 64 hexadecimal characters (a SHA-256 digest). It is
  stored lowercased. Anything else fails validation.
- No commands, executable paths, scripts, window rules, or positioning actions. There is no field
  for them.

**Cofferdam still does not move windows.** M2B does enumerate the connected displays
([`RUNTIME_INVENTORY.md`](RUNTIME_INVENTORY.md)), so an entry here can now be matched to a panel
that was actually found — which is what the schema was waiting for. `preferred_display_id` on a
browser profile is still metadata only — see [`APPLICATION_PROFILES.md`](APPLICATION_PROFILES.md).

### Display identity

Recorded before discovery existed; **implemented in M2B**:

- **Prefer a hardware fingerprint**: the SHA-256 of the panel's EDID together with the owning
  host. That survives cables, ports, and reboots.
- **`connector_hint` is a runtime hint, never identity.** `DP-1` becomes `DP-2` when a cable
  moves; treating it as identity silently retargets whatever was pointed at it.
- **A discovered display needs no label.** Labels are overlays, addable at discovery time or any
  time afterwards, and a display without one is completely normal.

### How an entry here is matched to a discovered display

The resolver attaches this entry's `name` and `aliases` to a discovered display only on
**hardware-grade** evidence:

- `match.edid_sha256` equal to the discovered panel's EDID digest, **or**
- `match.manufacturer` + `match.model` + `match.serial` — all three present, all three equal.

**`match.connector_hint` on its own never matches.** It is a socket, not a panel: plug a different
monitor into `HDMI-1` and the hint still fits while the hardware does not. Matching on it would
silently move a label onto somebody else's monitor. The field exists to help a human write the file
and to disambiguate for a person reading it.

Ambiguity fails closed. If two entries match one display, or one entry matches two displays,
neither is applied and the display reports why it stayed unlabelled.

A matched overlay **adds** a label. It never replaces the display's `resource_id`, connector,
manufacturer, model, serial, or fingerprint, and deleting the entry leaves the display fully
identified.

**Editing labels from the UI is M2B2.** In this build the file is still authored by hand.

---

## Reading them

```
GET /api/registries              → per-registry version, item counts, load/validation status
GET /api/registries/devices      → the validated registry
GET /api/registries/displays
```

Both require the same device token as every other state-revealing route; unauthenticated requests
get `401`. There is no write endpoint.

For what is actually plugged in right now — connector, model, resolution, refresh rate, physical
size, and an EDID fingerprint — use the runtime inventory instead:

```
GET /api/runtime/displays        → the displays this session is driving, discovered live
```

Errors are bounded and structured. A configuration failure reports the registry, a structural
location such as `items[3].match.edid_sha256`, and a code-owned explanation — never a filesystem
path, never file content, never raw exception text.
