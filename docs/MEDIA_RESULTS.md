# Official-provider search and result selection (M2B3A.1)

M2B3A could open Spotify and open a service's search page. It could not answer
*"which of these is the one I meant?"* — opening a search page hands that
question back to the user, on a screen they are not looking at.

This milestone answers it for **Spotify** and **YouTube**, through official
provider APIs only. Nothing here scrapes a website, reads a browser profile,
inspects cookies, or automates a page.

## What it does, and what it still does not

| | |
| --- | --- |
| Search a provider's real catalogue | ✅ official API |
| Show up to 5 distinguishable results | ✅ |
| Open the exact item you chose | ✅ |
| Open the first result, when you ask | ✅ explicit button |
| Open the first result automatically | ❌ deliberately not built |
| Start playback | ❌ and never claimed |
| Control playback / devices | ❌ unreachable by construction |
| Netflix / Prime Video / TV+ results | ❌ unchanged from M2B3A |

## Provider requirements, as verified on 2026-08-05

### Spotify

* **Search** — `GET https://api.spotify.com/v1/search`, `q` + `type` required;
  `limit` accepts 0–10 and defaults to 5, so one call answers one search.
* **Authorization** — client credentials against
  `https://accounts.spotify.com/api/token`: HTTP Basic with the client id and
  secret, `grant_type=client_credentials`, token valid about an hour. The
  documentation is explicit that this flow reaches **only endpoints that do not
  access user information**.
* **Development mode** — a new app starts there. The five-user allowlist applies
  to *authenticated user* tokens; a client-credentials token carries no user, so
  it does not apply to catalogue search. The shared quota bucket does, and shows
  up as a `429`.
* **Result types** — `album`, `artist`, `playlist`, `track`, `show`, `episode`,
  `audiobook`. Cofferdam offers all but `audiobook`, whose availability is
  market-dependent; offering a type that silently returns nothing in a user's
  market is worse than not offering it.
* **Item identity** — every object carries `uri` as `spotify:<type>:<id>`.

**That authorization model is the strongest guarantee in this milestone.**
Playback control is not omitted by restraint — the token Cofferdam holds cannot
reach a playback endpoint at all.

### YouTube

* **Search** — `GET https://www.googleapis.com/youtube/v3/search` with
  `part=snippet`; `type=video` restricts to videos; `maxResults` 0–50, default 5.
* **Authorization** — an **API key** is sufficient. OAuth is required only for
  `forContentOwner` / `forDeveloper` / `forMine`, none of which are used, so no
  user account is involved and no user data is reachable.
* **Quota** — the documented default allocation is **100 `search.list` calls per
  day**, alongside 10,000 units/day for other endpoints. A real user will meet
  this, so it has its own error state and its own sentence on the phone.
* **Response** — `items[].id.kind` (`youtube#video`), `items[].id.videoId`, and
  `snippet.title` / `channelTitle` / `publishedAt` / `liveBroadcastContent`.
* **Video ids** — Google publishes no formal grammar. Cofferdam enforces the
  universally observed shape (11 chars of `[A-Za-z0-9_-]`) as a *defensive*
  bound, documented as such rather than as a guarantee, because this value
  becomes part of a URL handed to a browser.

**Duration is deliberately absent.** `search.list` does not return it; it needs a
second `videos.list` call, and that is a network round trip on the phone's
critical path for a field nobody picks a video by.

## Credentials

Local file, owner-only, in the directory the device token already uses:

```
$COFFERDAM_HOME/secrets/media_providers.json      (0600, inside a 0700 dir)
```

```json
{
  "spotify": { "client_id": "…", "client_secret": "…" },
  "youtube": { "api_key": "…" }
}
```

No new mechanism was invented — the repository already had a reviewed answer to
"where does a local secret go", and a second one is how a project ends up with a
secret in the place nobody audits.

**There is no credential form in the PWA.** Typing a key into the phone would
mean the secret travels in a request body, sits in a text input, and lands in a
file the web tier can write. The repository has no reviewed secure secret-entry
mechanism over the network, and this is not the milestone to invent one.

### What may be observed

`GET /api/media/diagnostics` returns **one status word per provider** —
`configured`, `missing`, `invalid`, `provider_rejected`,
`temporarily_unavailable` — and nothing else. There is no API, not even an
internal one, that returns a credential value, prefix, suffix, length, or hash.
The credential file's *path* is not returned either: this document names it, and
an API response is not the place to publish a host's filesystem layout.

Two warnings are surfaced because they can be said without revealing anything:
the credential file being readable by other users, and a provider key being
present in the service's environment (where Cofferdam does not read it, and
where `/proc/<pid>/environ` and crash dumps would expose it).

### Setup

**The full step-by-step guide is [`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md)** —
console walkthroughs for both providers, the exact file schema and permissions,
how to validate the configuration without printing any credential value, and
troubleshooting for each provider state. The summary below is the shape of it.

**Spotify** — <https://developer.spotify.com/dashboard> → create an app →
tick **Web API** → copy the client id and secret. The dashboard requires a
redirect URI to save the form, but the client-credentials flow does not use one,
so its value is not part of the catalogue-search path. Development mode is fine;
the user allowlist does not affect catalogue search.

**YouTube** — <https://console.cloud.google.com/> → create a project → enable
**YouTube Data API v3** → create an **API key** → restrict it to that API, and
leave *"Authenticate API calls through a service account"* disabled, since
Cofferdam sends an API key rather than a signed JWT.

Then, on the workstation:

```bash
install -m 700 -d ~/cofferdam/secrets
install -m 600 /dev/null ~/cofferdam/secrets/media_providers.json
# edit it in a local editor and paste the values there
```

Creating the file empty and editing it keeps every secret out of shell history.
The file is re-read per request, so a correction takes effect without a restart
— and a *removed* credential stops working at once rather than lingering in a
process that started before the removal.

## The search-session model

The tempting shortcut is to send each result's URI to the phone and let the
phone send it back on tap. That would make Cofferdam accept a caller-supplied
URI — exactly the capability the typed-action boundary exists to withhold.

So the server remembers instead:

```
POST /api/media/providers/{provider_id}/results/search   {"query": "…", "types": ["track"]}
  → {"search_id": "…", "observed_at": "…", "expires_at": "…", "results": [ … ]}

POST /api/media/searches/{search_id}/results/{result_id}/open   {"provider_id": "…"}
POST /api/media/searches/{search_id}/results/first/open         {"provider_id": "…"}
```

Each session holds the results **and** a private `ProviderItem` per result. The
client receives opaque handles; the launch target never travels through it.

| bound | value |
| --- | --- |
| TTL | 600 s |
| concurrent sessions | 32, oldest-first eviction |
| results per session | 5 |
| persistence | none — in memory, gone on restart |

Sessions dying with the process is a feature. A search and its results reveal
what someone was looking for, and a restarted daemon that still honoured old
`search_id` values would be claiming knowledge it no longer has. The client
already handles the same "search again" response for a timed-out session.

`provider_id` on the open request is the client's *assertion* of which provider
the card belongs to. The server refuses when it disagrees with the session —
which is what stops a YouTube video id from being routed into the Spotify
native-URI adapter.

## Result normalization

A versioned, provider-neutral model (`result_model_version: 1`). Every result is
constructed field by field; **no provider dictionary is ever copied wholesale**,
so a field that is not in the model does not reach the client.

Present: `provider_id`, `result_id`, `result_type`, `title`, `subtitle`,
`creators`, `duration_seconds`, `published`, `explicit`, `live_state`,
`provider_metadata`, `selectable`, `open_action_supported`.

Absent by design: access tokens, authorization data, cookies, user-account data,
internal provider URLs, HTML, tracking parameters — **and any playable URL or
URI**. A result the client could read a target out of would let it skip the
server's resolution step entirely.

Bounds: title/subtitle 200 chars, creator names 120 chars and at most 4,
metadata 6 entries of 80 chars, 5 results. Display text is *truncated* with an
ellipsis rather than rejected, so a very long title stays pickable and never
passes for a complete one.

Provider titles arrive HTML-escaped from YouTube (`&amp;`) and are **not**
unescaped — they are carried as text and escaped again on render, so there is no
step at which a provider could produce markup.

## Network behaviour

One module (`mediasearch/transport.py`) talks to the internet, using
`http.client` and `ssl` from the standard library:

* **fixed hosts** — `accounts.spotify.com`, `api.spotify.com`,
  `www.googleapis.com`. No parameter accepts a full URL.
* **no redirects, ever** — a 3xx is a failure, not a hop. This is the single
  most important line in the file: a followed redirect is how a host allowlist
  becomes a *first*-host allowlist, and how a request carrying an
  `Authorization` header delivers it somewhere else. It also makes "cannot reach
  a private address" structural — there is no second connection to make.
* **verified TLS only** — `HTTPSConnection`, port 443, `CERT_REQUIRED`,
  hostname checking. No parameter weakens either.
* **bounded** — 6 s connect, 8 s read, 512 KiB response read as `read(n+1)` so
  an over-long body is *detected* rather than truncated into malformed JSON.
* **no proxy** — `http.client` does not consult `http_proxy`/`https_proxy`, so
  the environment cannot redirect these calls.
* **no retry loop** — one attempt. Spotify gets a single re-auth retry for one
  specific cause (a cached token that expired mid-flight), and nothing more.

`Retry-After` is carried to the client as data. **Nothing sleeps on it** — a
daemon that blocked on a provider's Retry-After would hand that provider the
ability to stall the whole workstation.

Distinct handled states: credentials missing · credentials invalid · provider
rejected · quota exhausted · rate limited · timeout · provider unavailable ·
malformed response · zero results · search expired · search unknown · result
unknown · launch rejected.

## Opening a selected result

**Spotify** — the URI is *rebuilt*, not forwarded. The adapter validates the
type against a closed tuple and the id against the base-62 shape, keeps those
two values, and reconstructs `spotify:<type>:<id>` at open time, then hands it
to the narrow native-URI adapter from M2B3A. A forwarded string would mean the
thing given to a native application came from a network response; a
reconstructed one can only be two validated tokens joined by a constant.

**YouTube** — the video id is re-validated and the official watch URL is built
from a constant prefix, then routed through Opera by Cofferdam's default browser
selection. Explicit Firefox selection remains available through the existing
`browser_id` model. No tracking parameters, no playlist context.

Since M2E, opening a YouTube result in a normal watch tab is the **explicit
*Open in YouTube* action** rather than the default. A YouTube video result also
offers *Play now* and *Add to queue*, which send it to the one persistent
Cofferdam player instead of opening a tab — see
[`YOUTUBE_PLAYER.md`](YOUTUBE_PLAYER.md). The resolution path described here is
unchanged and is what both of those routes reuse: the client still names a
result, never a video.

**Neither claims playback.** Success means the launch was accepted and confirmed
to the M1 standard, and every media result carries `playback: not_started` and
`playback_started: false` — on success. The phone repeats that wording rather
than upgrading it.

## "Open first result"

An explicit button, never automatic. Provider ranking is an opinion, and acting
on it unasked is how the wrong song opens. It takes index 0 of a verified
session, and a zero-result search has no first result — the button is not
rendered, and the route refuses.

**The persistent auto-open-first preference is deferred.** It would need a
settings surface this milestone does not have, and the brief itself says to
prefer the smaller truthful implementation. The capability is reported as
`auto_open_first_supported: false` so the phone need not guess, and turning it on
later is a change to one value rather than a new vocabulary.

## Privacy and audit

A search query and the titles it returns reveal what someone was looking for, so
they are treated as user content:

* **not written to daemon logs.** The `mediasearch` package contains no logging
  call, no `print`, and no `sys.stdout`/`stderr` write at all — asserted
  structurally by a test, so a query cannot reach a log by accident.
* **not persisted.** Sessions are memory-only and expire.
* **shown to the authenticated user**, in the result cards and in *Recent
  actions* — which is the existing privacy model for action parameters, and
  requires the device token.
* action records carry provider, operation, result status, result type, the
  search id, and timestamps.

Provider credentials appear in exactly one place: the `Authorization` header of
an outbound request, and the `key` query parameter YouTube's API requires. They
are in no response, no error, no action record, no argv, and no registry.

## Why Netflix, Prime Video and TV+ are still out of scope

They publish no official public catalogue-search API for this purpose. Getting
structured results from them would mean scraping or DOM automation, which this
project does not do. Their M2B3A behaviour is unchanged, and the code makes it
structural: their catalogue entries carry no `structured_search_key`, so they
cannot acquire structured search even if the credential store were somehow told
they were configured.

The next milestone, **M2B3A.2 — Opera Companion foundation**, is where that
becomes addressable: an approved companion could identify the already-signed-in
service tab and perform a *semantic* search within it, returning real result
cards the user picks from. Still no coordinates, no OCR, no screenshots, and no
blind first-result clicking.

## Related

* [`SPOTIFY_PLAYBACK.md`](SPOTIFY_PLAYBACK.md) — M2D, which plays a verified *track* result
  through the user's own Spotify account. It reuses these search sessions rather than adding a
  second catalogue search, and the client still sends only the handles issued here.
* [`MEDIA_PROFILES.md`](MEDIA_PROFILES.md) — the M2B3A launch surface this builds on
* [`APPLICATION_PROFILES.md`](APPLICATION_PROFILES.md) — browser routing and `browser_id`
* [`DECISIONS.md`](../DECISIONS.md) — D-2026-08-05-7 and -8
