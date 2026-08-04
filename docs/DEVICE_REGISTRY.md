# Device and display registries

Two of the six registries described in [`CONTROL_PLANE.md`](CONTROL_PLANE.md). Both are
**descriptive**: they say what exists and what it is called. Neither can say how to reach it or
what to do to it.

Files:

```
$COFFERDAM_HOME/config/registries/devices.json
$COFFERDAM_HOME/config/registries/displays.json
```

Committed placeholders: [`examples/registries/`](../examples/registries/). The real files are
machine-specific and are never committed.

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

```json
{
  "id": "ubuntu-workstation",
  "name": "Ubuntu workstation",
  "aliases": ["bilgisayar", "ana bilgisayar"],
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

```json
{
  "id": "large-monitor",
  "device_id": "ubuntu-workstation",
  "name": "Büyük monitör",
  "aliases": ["büyük ekran", "ana monitör"],
  "enabled": true,
  "match": {
    "connector_hint": "DP-1",
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

**M2A does not move windows.** The schema exists so that later voice or text input can resolve
"büyük monitör" to `large-monitor`, and so that a display can be recognised again after a cable
change. `preferred_display_id` on a browser profile is metadata only — see
[`APPLICATION_PROFILES.md`](APPLICATION_PROFILES.md).

---

## Reading them

```
GET /api/registries              → per-registry version, item counts, load/validation status
GET /api/registries/devices      → the validated registry
GET /api/registries/displays
```

Both require the same device token as every other state-revealing route; unauthenticated requests
get `401`. There is no write endpoint in M2A.

Errors are bounded and structured. A configuration failure reports the registry, a structural
location such as `items[3].match.edid_sha256`, and a code-owned explanation — never a filesystem
path, never file content, never raw exception text.
