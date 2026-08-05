# Media and application launch profiles (M2B3A)

What this milestone adds: a phone can open Spotify, YouTube, Netflix, Prime Video and TV+, and can
send a search to the ones that support it. What it does **not** add: playback, playback control,
result selection, sign-in, or any automation inside a page.

That second list is not a gap to be apologised for. It is the boundary that makes the first list
honest, and everything below is organised around keeping the two apart.

## The three layers, again

Cofferdam already separates *configuration* (what you wrote down), *definitions* (what the code can
launch), and *runtime* (what is actually running). Media providers are **definitions**.

Opening Netflix opens a page in a browser you already had. It does not create a "Netflix"
application on the machine, so nothing in the runtime inventory grows a Netflix entry — a media
definition becomes a running instance only when real discovery finds a real process. The PWA keeps
them in separate panels for the same reason.

## The provider model

Each provider is a frozen entry in `cofferdam/workstation/media.py`:

| field | meaning |
| --- | --- |
| `id` | stable, allowlisted; the only way a client names a provider |
| `name` | what a person sees |
| `kind` | `native_app` or `web_service` |
| `supported_actions` | `open`, and `search` only where a route exists |
| `application_key` | native only; a key from the adapter's allowlist, never a program |
| `home_url` | web only; a constant destination |
| `browser_key` | web only; which browser opens it (Opera) |
| `limitations` | shown verbatim on the card — where "this does not play anything" is said |
| `search_unavailable_reason` | why search is missing, when it is |

### Why the catalogue is code and not a registry

The M2A registries are descriptive by construction: they can say "this machine has an application
called Opera" and cannot say "run `/snap/bin/opera --user-data-dir=…`". A media provider *is* a URL.
Putting providers in a JSON file would therefore hand that file the one power the registry boundary
exists to withhold — aiming a browser at an arbitrary address.

So the ids, the home targets and the search builders are constants in source. Adding a provider
means editing that file and shipping a build. In exchange, **no caller anywhere** — API client, PWA,
or registry file — can supply a URL, a template, a query-parameter name, a scheme, or a program.
The request schemas have no field for any of them, and `extra="forbid"` rejects a smuggled one
before an adapter is reached.

## Typed actions

```
open_media_provider(provider_id)
search_media_provider(provider_id, query)
open_url(url, browser_profile_id=…, browser_id=…)
```

`query` is plain human text: stripped, non-empty, at most 120 characters, and free of control
characters (C0, DEL, C1, and U+2028/U+2029 — not merely `\n`). It is rejected rather than sanitised,
because silently stripping a character would run a search for something the user did not type.

The catalogue then percent-encodes it into a route the catalogue owns. `quote_plus` for
query-string values, `quote(safe="")` for the Spotify URI; both encode `/ ? & = #`, so a phrase can
never grow a second parameter, a path segment, or a fragment.

Everything reaches the host as one element of a fixed argv vector, through the systemd user manager,
with no shell and no string concatenation — the same path M1 established for URLs.

## What "success" means

A media action succeeds when **a launch was accepted and confirmed by the adapter**, to the standard
M1 set: the process survived its settle window, or a running instance of the same application can be
seen to have taken the request over.

It does not mean a video started. It does not mean a track is playing. Every media result therefore
carries:

```json
{"playback": "not_started", "playback_started": false, "note": "…"}
```

on success, and the phone's toast repeats the note rather than upgrading it to something greener.

## Host findings (read-only investigation, 2026-08-05)

| question | finding |
| --- | --- |
| Opera | snap, 133.0.5932.85, `/snap/bin/opera`, running |
| Firefox | snap, `/usr/bin/firefox`, present |
| Spotify | snap, `/snap/bin/spotify`, real desktop application |
| OS default browser | already `opera_opera.desktop`; **not changed by this milestone** |
| Opera app-mode | **not available** — the build exposes `app-id`, `new-window` and `incognito`, but no `--app` switch |
| Spotify URI handling | `MimeType=x-scheme-handler/spotify`, `Exec=… %U` — the `spotify:` scheme is a registered entry point |
| Widevine/DRM | **not bundled** in the Opera snap image; Opera fetches a CDM at runtime, which read-only inspection cannot confirm without reading the user's profile. Treated as unverified and not claimed anywhere. |

No packages were installed, and no browser profile, cookie store, history, or account data was read.

### Search routes, as measured

| provider | route | result |
| --- | --- | --- |
| YouTube | `/results?search_query=` | 200 — shipped |
| Netflix | `/search?q=` | 302 to sign-in **preserving the search as `nextpage`** — shipped |
| Prime Video | `/search?phrase=` | 200 — shipped |
| TV+ | `/search?term=` | 302 to the storefront root, **query discarded** — *not* shipped |
| TV+ | `/{storefront}/search?term=` | 200, but needs a region Cofferdam cannot determine |

TV+ is the case worth keeping in mind. Building "search" on an address that silently drops the query
would have produced a page that opens successfully and shows the wrong thing — a green result for a
search that never happened. It exposes **Open** only, and says why on the card.

## Opera as the default

See [`DECISIONS.md`](../DECISIONS.md) D-2026-08-05-5. In short: Cofferdam opens links in Opera;
Firefox stays explicitly selectable; the operating system's own default browser and file
associations are untouched; a configured profile still outranks the product default; and a host
without Opera behaves exactly as it did before.

## Future adapter seams — documented, not implemented

Neither of these exists in this build. They are written down so the current boundaries are
recognisably deliberate rather than unfinished.

> **Partly built since M2B3A.1.** Official catalogue search, result cards, user selection and
> opening an exact item now exist for Spotify and YouTube — see
> [`MEDIA_RESULTS.md`](MEDIA_RESULTS.md). Still unbuilt: Spotify playback control, and the whole
> browser companion.

### Spotify semantic adapter

Would add: OAuth consent, catalog search against the Web API, ranked result cards, user selection,
opening an exact `spotify:track:…` URI, and — where account tier and API eligibility permit —
play/pause/next and device selection.

*(Catalogue search and exact opening landed in M2B3A.1, using **client credentials** rather than
OAuth consent. That flow reaches only endpoints which do not access user information, which is why
playback control there is not merely unimplemented but unreachable. Adding it would mean adopting
the authorization-code flow — a milestone with its own review.)*

The rule it inherits: **never silently resolve an ambiguous result.** If "play that song from the
advert" matches four tracks, the four are shown and the user picks. An adapter that guessed and
played would be the audio version of the false success this project already refuses once. M2B3A.1
follows it: five cards, an explicit pick, and an "Open first result" button that is never automatic.

### Browser companion

Would add, for Netflix / Prime Video / TV+ / YouTube: identifying the already-authenticated service
tab, performing a semantic search through an approved extension or companion, returning real result
cards, and navigating to a title the user chose via verified DOM actions.

The rule it inherits: **semantic actions only.** No coordinates, no OCR, no screenshots, no blind
"click the first result". It would also be the natural place to settle OQ-3, since a companion can
read the storefront region from the tab that is already open.

## Out of scope here

Closing, restarting or terminating application instances; process signals; Agent Task Core;
natural-language parsing. Safe close/restart is M2B3B.
