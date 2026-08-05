# Spotify and YouTube credential setup

Step-by-step setup for the official-provider search shipped in M2B3A.1. For what
that feature does and why it is built the way it is, see
[`MEDIA_RESULTS.md`](MEDIA_RESULTS.md); this document is only the setup path.

Both providers are optional and independent. Configuring neither leaves
Cofferdam fully working — searching simply reports `missing` for the provider
you have not set up, which is a truthful state and not an error.

---

## Before you start: where credentials must never go

A provider secret is a bearer credential. Anyone holding it can spend your
quota, and a Spotify client secret can be used to mint tokens until you rotate
it. Keep these values out of:

* **chat messages** — including messages to an AI assistant. Nothing about
  setting Cofferdam up requires you to paste a secret into a conversation, and
  no part of this guide asks you to.
* **Git commits** — including a file you intend to delete in the next commit.
  Git keeps history; a secret committed once is committed permanently until the
  history is rewritten and the credential rotated.
* **screenshots** — the Spotify dashboard shows the secret in full once you
  reveal it. Crop or close it before capturing anything.
* **shell commands** — `echo "secret" > file` and
  `export SPOTIFY_CLIENT_SECRET=…` both land in `~/.bash_history`, and the
  environment variable is then readable from `/proc/<pid>/environ` and lands in
  crash dumps. Use an editor instead.
* **URLs** — query strings are logged by every proxy and server in the path and
  are kept in browser history.
* **logs** — Cofferdam never logs credential values; make sure your own
  debugging does not either.

If a secret does end up somewhere it should not: **rotate it first**, then clean
up. Cleanup without rotation leaves the credential valid.

Cofferdam is built so that following this guide is enough. There is no
credential form in the PWA, because typing a key into the phone would put the
secret in a request body, in a text input, and in a file the web tier can write.
There is no API — not even an internal one — that returns a credential value,
prefix, suffix, length, or hash.

---

## 1. Spotify

Spotify is used for **catalogue search only**. Cofferdam authenticates with the
Client Credentials flow, whose token by design reaches only endpoints that do
not access user information. It cannot read your library and it cannot control
playback. (Playback arrives in a later milestone, with its own user OAuth
consent and its own review.)

### 1.1 Create the application

1. Sign in at <https://developer.spotify.com/dashboard>.
2. Choose **Create app**.
3. Give it a name and description. These are yours; anything recognisable is
   fine, such as `Cofferdam workstation`.
4. **Redirect URI** — the dashboard requires *something* in this field to save
   the form, but **Cofferdam never uses it**. The Client Credentials flow has no
   browser redirect and no user consent step, so the value is not part of the
   catalogue-search path at all. `http://localhost:8080/callback` is a
   conventional placeholder. If a later milestone adds user OAuth for playback,
   it will document its own redirect URI requirement then.
5. Under **Which API/SDKs are you planning to use?**, tick **Web API**. That is
   the API `/v1/search` belongs to. You do not need the Web Playback SDK, the
   iOS/Android SDKs, or Ads API.
6. Accept Spotify's terms and save.

### 1.2 Obtain the Client ID and Client Secret

1. Open the app you just created and go to **Settings**.
2. The **Client ID** is shown directly.
3. Choose **View client secret** to reveal the **Client Secret**.
4. Copy both straight into the editor you will use in step 3 — not into a chat
   window, a note-taking app that syncs, or a terminal command.

### 1.3 Development mode is fine

A new Spotify app starts in development mode. Its five-user allowlist applies to
*authenticated user* tokens; a client-credentials token carries no user, so the
allowlist does not affect catalogue search. The shared quota bucket does apply
and shows up as rate limiting — see troubleshooting.

---

## 2. YouTube

YouTube is used for **video search only**, authenticated with an API key. No
Google account is linked, no OAuth consent happens, and no user data is
reachable. OAuth would only be required for `forContentOwner`, `forDeveloper`
and `forMine`, none of which Cofferdam uses.

### 2.1 Create a Google Cloud project

1. Sign in at <https://console.cloud.google.com/>.
2. Open the project picker in the top bar and choose **New project**.
3. Name it something recognisable, such as `cofferdam`, and create it.
4. Make sure the new project is the one selected in the top bar before
   continuing — enabling an API in the wrong project is the most common way this
   setup goes wrong.

### 2.2 Enable YouTube Data API v3

1. Go to **APIs & Services → Library**.
2. Search for **YouTube Data API v3** and open it.
3. Choose **Enable**.

### 2.3 Create an API key

1. Go to **APIs & Services → Credentials**.
2. Choose **Create credentials → API key**.
3. The key is displayed once in a dialog. Copy it into your editor now.

**Leave "Authenticate API calls through a service account" disabled.** If the
console offers this option, do not enable it. A service account is a different
credential type with a different flow — it issues signed JWTs rather than a
simple key — and Cofferdam's YouTube client sends an API key. Enabling it does
not improve security here and produces a credential Cofferdam cannot use.

### 2.4 Restrict the key to YouTube Data API v3

An unrestricted key works with every enabled API in the project, so a key that
leaks is worth more than it needs to be. Restrict it:

1. On the **Credentials** page, open the key you just created.
2. Under **API restrictions**, choose **Restrict key**.
3. Select **YouTube Data API v3** from the list, and nothing else.
4. Leave **Application restrictions** set to **None**. The alternatives are
   HTTP referrer, IP address, and mobile app restrictions; Cofferdam calls the
   API from the workstation itself, and a home connection's IP address usually
   changes, so an IP restriction here tends to break the feature rather than
   protect it.
5. Save. Restrictions can take a few minutes to take effect.

---

## 3. Write the credentials file on the workstation

Cofferdam reads one local file, owner-only, in the directory the device token
already uses. No new secret-storage mechanism was invented — a second one is how
a project ends up with a secret in the place nobody audits.

### 3.1 Create the directory and file with the right permissions

Run this on the workstation. It creates an **empty** file with correct
permissions; no secret appears in any command, so nothing lands in shell
history:

```bash
install -m 700 -d ~/cofferdam/secrets
install -m 600 /dev/null ~/cofferdam/secrets/media_providers.json
```

* the `secrets` directory must be mode **0700** (owner-only)
* `media_providers.json` must be mode **0600** (owner read/write only)

### 3.2 Paste the values in a local editor

```bash
nano ~/cofferdam/secrets/media_providers.json
```

Use any local editor. The point is that the values are typed into a file, never
into a command line.

### 3.3 The exact schema

```json
{
  "spotify": {
    "client_id": "your Spotify client id here",
    "client_secret": "your Spotify client secret here"
  },
  "youtube": {
    "api_key": "your YouTube API key here"
  }
}
```

Rules:

* Both top-level keys are optional. Include only the providers you configured;
  omitting one leaves it `missing` and leaves the other fully working.
* All three values are strings.
* No other fields are read. Extra keys are ignored rather than treated as
  configuration.
* The placeholder text above is descriptive on purpose. Replace each string with
  the real value from the provider console — this repository contains no real
  credentials, no credential prefixes, and no realistic-looking example secrets
  anywhere, and neither should your commits.

The file is re-read per request, so a correction takes effect without restarting
the service — and a *removed* credential stops working immediately rather than
lingering in a process that started before the removal.

---

## 4. Validate, without printing anything secret

### 4.1 Check the permissions

```bash
stat -c '%a %n' ~/cofferdam/secrets ~/cofferdam/secrets/media_providers.json
```

Expected: `700` for the directory and `600` for the file. Anything more
permissive means other accounts on the machine can read your credentials.

### 4.2 Check the file is valid JSON and has the right shape — without values

This prints only which keys are present, never what they contain:

```bash
python3 -c "import json,pathlib;d=json.loads(pathlib.Path.home().joinpath('cofferdam/secrets/media_providers.json').read_text());print({k:sorted(v) for k,v in d.items()})"
```

Expected output:

```
{'spotify': ['client_id', 'client_secret'], 'youtube': ['api_key']}
```

A `JSONDecodeError` here means a syntax problem — most often a trailing comma or
a smart quote pasted from a web page.

### 4.3 Ask Cofferdam

```bash
curl -sS -H "Authorization: Bearer $(cat ~/cofferdam/secrets/token)" \
  http://127.0.0.1:8765/api/media/diagnostics
```

This returns **one status word per provider** and nothing else. The endpoint
cannot return a credential value, and it does not return the credential file's
path either — this document names it; an API response is not the place to
publish a host's filesystem layout.

The same status is visible in the PWA's Media panel, which is the easier check
if you are already on the phone.

Two warnings are surfaced here because they can be said without revealing
anything: the credential file being readable by other users, and a provider key
being present in the service's environment, where Cofferdam does not read it and
where `/proc/<pid>/environ` and crash dumps would expose it.

---

## 5. Troubleshooting by provider state

The first five are **credential statuses** from
`GET /api/media/diagnostics`. The last two are **runtime conditions** that
appear when a search is actually run.

### `configured`

The credentials are present and structurally valid. This is a statement about
the *file*, not proof the provider has accepted them — that is only known once a
search runs.

### `missing`

No entry for this provider. Either the file does not exist, or it has no
top-level key for this provider. Re-check step 3.1 (was the file created where
you think?) and 3.3 (is the provider key spelled `spotify` / `youtube`?).

### `invalid`

The file exists but this provider's entry cannot be used: it is not valid JSON,
the provider's value is not an object, a required field is absent, or a value is
empty or not a string. Run the check in step 4.2 — it distinguishes a JSON
syntax error from a missing field.

### `provider_rejected`

The provider refused the credentials. They are structurally fine and wrong.

* **Spotify** — the client id and secret do not match, or the secret was rotated
  in the dashboard. Regenerate the secret and repeat step 1.2. A common cause is
  copying the client id into both fields.
* **YouTube** — the key is wrong, the key's API restriction does not include
  YouTube Data API v3, or **YouTube Data API v3 is not enabled in the project
  the key belongs to**. Confirm step 2.2 and step 2.4, and confirm you were in
  the intended project. Also note that new restrictions take a few minutes to
  propagate, so a key edited seconds ago may still be refused.

### `temporarily_unavailable`

Cofferdam could not reach the provider, or the provider returned a server error.
This is not a credential problem and needs no change on your side. Check the
workstation's own connectivity; retry shortly.

### `rate_limited`

Too many requests in a short window; the provider asked for a pause. Spotify
applies this per-app, and a development-mode app shares a quota bucket, so a
burst of searches can trigger it. Cofferdam surfaces the provider's retry hint
where one is given. Wait and retry — nothing is misconfigured.

### `quota_exhausted`

The daily allowance is used up. In practice this is a **YouTube** state: the
documented default allocation is about **100 `search.list` calls per day**, and
a real user meets it. Cofferdam reports it through the same rate-limited
channel, with a message naming the quota specifically, because Google returns it
as a `403` whose machine-readable `reason` distinguishes it from key rejection
(`quotaExceeded` and `dailyLimitExceeded` rather than a bad key).

The allowance resets at **midnight Pacific time**. If you need more, request a
quota increase in the Google Cloud console — but 100 searches a day is a lot of
searching for one household, and hitting it repeatedly is worth understanding
before it is worth raising.

---

## Related

* [`MEDIA_RESULTS.md`](MEDIA_RESULTS.md) — what the search feature does, the
  provider requirements as verified on 2026-08-05, and the search-session model.
* [`MEDIA_PROFILES.md`](MEDIA_PROFILES.md) — which services Cofferdam can open
  and how they are launched.
* [`AUDIO_CONTROL.md`](AUDIO_CONTROL.md) — system volume, mute, and output
  selection, which are separate from any provider's own player volume.
