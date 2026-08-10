# The private Custom GPT Actions bridge (M2I.5)

A separate, narrow process that lets a **private Custom GPT** delegate work to
Cofferdam and read back what happened. It publishes eight bounded Actions and
reaches Cofferdam through ten fixed internal calls.

**Status as of M2I.5 PR2: exposed and connected.** The bridge still binds to
loopback — that has not changed and must not. What changed is that a Cloudflare
Tunnel now reaches that loopback port from one dedicated hostname, and a real
private Custom GPT is configured against it.
[Gate A](#gate-a--external-exposure) is closed; see
[`ACTIONS_EXPOSURE.md`](ACTIONS_EXPOSURE.md) for what was deployed and how to
roll it back.

---

## What it is, and what it is not

```
  User
    ↕
  Private Custom GPT              ← planning, interpretation, conversation
    ↕  bounded HTTPS Actions
  Cofferdam Actions bridge        ← this process: translate and refuse
    ↕  fixed authenticated internal client
  Cofferdam Task Core             ← the authority: lifecycle, projects, results
    ↕
  Claude Agent SDK / Claude Code adapter
```

The Custom GPT is a **client**. It may ask; it never decides. That is
D-2026-08-08-2 restated for the case where the client is somebody else's
product.

The bridge is **not**:

- the Project Workstation PWA,
- the main Cofferdam API,
- a reverse proxy or a generic HTTP proxy,
- a mirror of the private `/api/tasks` routes,
- a provider adapter, a shell runner, a filesystem API, or a transcript API.

It has no route that forwards a caller's path, method, header or query string.
The only way out of the process is ten named methods on
[`InternalTaskClient`](../cofferdam/actions_bridge/internal.py).

## Trust boundaries

There are three, and they are enforced by different mechanisms.

### 1. The external boundary — a model provider

Everything arriving here is untrusted, including text a model composed on the
user's behalf. Enforced by: a dedicated Bearer key, a closed request vocabulary
(`additionalProperties: false` in the schema, an allowlist in `_bridge_body`),
size and rate limits, and identifier grammars checked before any use.

### 2. The internal boundary — the bridge to the daemon

The bridge holds a credential the daemon recognises on **ten task routes only**.
Enforced structurally: the daemon's other ~60 routes use `require_token`, which
has never heard of the bridge credential, so a bridge request to
`/api/remote-control/...` is a 401 rather than a check that could be relaxed.

### 3. The provenance boundary — who asked

A task the bridge created is recorded as `origin = chatgpt_app` and its turns as
`source = future_gpt_bridge`. The PWA's work stays `pwa` / `workstation_pwa`. No
client can choose either value: they are derived from which credential
authenticated the request, and no route has a field for them.

## Credentials

Three secrets, three different jobs. None is ever the same bytes as another.

| Credential | Held by | File | Blast radius |
|---|---|---|---|
| Device token | The phone/PWA | `secrets/token` | The whole private API |
| **Bridge internal token** | The bridge process | `secrets/actions-bridge-internal-token` | Ten task routes |
| **Bridge external key** | The Custom GPT | `secrets/actions-bridge-key` | The eight Actions |

Both bridge files are mode `0600` and both are checked at startup. A file that
is group- or world-readable is a **startup failure, not a warning, and not
something the bridge corrects** — a secret that was briefly readable may already
have been read, and quietly tightening it would hide that.

The external key never reaches Cofferdam. The internal token never leaves the
machine, never appears in a response, and is never returned by any method on the
client that holds it.

### Why a second internal credential

The daemon's task routes assign `origin` and `source` from the authenticated
caller. Had the bridge reused the device token, every bridge-created task would
have been recorded as though somebody used their phone — a provenance falsehood
in exactly the direction `tasks/clarifications.py` was written to prevent.

The scoped credential also means **revocation is a file deletion**: remove
`secrets/actions-bridge-internal-token` and the bridge loses the daemon while
the phone keeps working.

It is off by default. The daemon generates it only under
`--enable-actions-bridge-caller` (or the matching config key / environment
variable), so a deployment that has not turned this on has no such file.

### Rotation

**External key** — the one the Custom GPT holds:

```bash
python -m cofferdam.actions_bridge --generate-key --force
```

The value is never printed. Read the file to copy it into the GPT editor, then
restart the bridge. The old key keeps working until that restart, deliberately:
the running process closed over the value at startup, so a half-written file
cannot lock the bridge out mid-flight.

**Internal token** — delete `secrets/actions-bridge-internal-token` and restart
the daemon, then restart the bridge. Both processes read the same path.

## The eight Actions

| operationId | Method | Path | Consequential |
|---|---|---|---|
| `bridgeHealth` | GET | `/v1/health` | no (unauthenticated) |
| `listProjects` | GET | `/v1/projects` | no |
| `createTask` | POST | `/v1/tasks` | **yes** |
| `listRecentTasks` | GET | `/v1/tasks` | no |
| `syncTask` | GET | `/v1/tasks/{task_id}` | no |
| `submitChoiceAnswer` | POST | `/v1/tasks/{task_id}/answer` | **yes** |
| `sendFollowup` | POST | `/v1/tasks/{task_id}/followup` | **yes** |
| `cancelTask` | POST | `/v1/tasks/{task_id}/cancel` | **yes** |
| `finishTask` | POST | `/v1/tasks/{task_id}/finish` | **yes** |

`x-openai-isConsequential: true` on every write forces ChatGPT to ask before
calling and suppresses its "always allow" button.

### What has no Action, and never will

No approval. No tool decision. No permission mode, model, budget, tool list,
effort or MCP configuration. No shell. No path — and therefore no file read, no
artifact browse, no repository listing. No transcript, no event stream, no
provider session id, no Remote Control.

These are not disabled endpoints. They are absent, which is a stronger statement
than one that refuses.

## Response normalization

Every response is **constructed** in
[`normalize.py`](../cofferdam/actions_bridge/normalize.py) from named fields.
Nothing copies an upstream payload and deletes keys from it: a delete-list stops
covering a key the day somebody upstream adds one, and the key that gets added
is never the harmless one.

Never crosses the boundary, by never being read: `provider_session_id`, the task
prompt, raw task events, `correlation_id`, `lifecycle_revision`, `event_cursor`,
`resource_summary`, the project root, registry notes, option `value` strings,
adapter internals, transcripts, reasoning, tool inputs, commands, environment.

**The prompt is withheld at the daemon**, not filtered at the bridge. A bridge
request to `GET /api/tasks/{id}` gets a payload with no `prompt` key at all —
the bridge composed that text from somebody's ChatGPT conversation, and handing
it back would let a model provider re-read it on a schedule.

### Display references

Every task carries `display_ref` — `CF-` plus six uppercase hex, derived by
digest from the canonical id. It is display-only and one-way: **no Action
accepts one**, and there is no lookup back to a task id anywhere in the package.
A conversation that has lost the canonical id calls `listRecentTasks`.

A digest rather than a prefix of the id, because a task id's first characters
encode its creation time in milliseconds — a visible prefix would publish when
every task was made, in a field designed to be read aloud.

## Clarification handling

The bridge carries **one** question shape: `single_choice`, with options that
have Cofferdam-minted `option_id`s.

`submitChoiceAnswer` takes exactly one `option_id`. There is **no text field on
that route at all** — not optional, not validated-and-refused. Task Core's own
answer route accepts free text because a person at the PWA typed it; the bridge
does not offer that channel, so prose a model composed cannot reach a waiting
agent through a question's answer slot.

A display number is refused with an explanation rather than forwarded: option
ids begin with a letter, so `"2"` can never be one.

### Unsupported shapes

Free text, multiple choice, an unknown mode, or a single-choice question whose
options lost their ids all come back as:

```json
{
  "clarification_supported": false,
  "reason": "unsupported_question_shape",
  "local_action_required": true,
  "question": "…the real question text…",
  "options": []
}
```

The question text survives so somebody reading their phone knows what is being
asked. **Nothing is invented in its place** — no option list, no reduction of a
free-text question to yes/no, no best guess. A fabricated question is a wrong
answer delivered to an agent in somebody's name.

### "Other" and custom text

Not supported, and not approximated. An option labelled "Other" is submittable
as a plain choice; **there is no way to attach text to it**. The Custom GPT is
instructed to say so and point at the local surface rather than sending
`"Other: some prose"` or silently picking a neighbouring option.

Supporting it later requires the provider's answer schema to be verified from
real evidence, not a guessed SDK payload.

## Tool approvals are excluded

A clarification is the agent asking for *information*. A tool approval is the
agent asking for *permission to act*. Cofferdam keeps them structurally apart:
the private API has **no approval route**, so there is nothing for the bridge to
expose.

When a task is waiting on one, `syncTask` reports
`local_action_required: true` with `local_action_reason: "approval"` and
`next_recommended_operation: "open_the_local_cofferdam_surface"`. The Custom GPT
reports it and cannot satisfy it.

`authentication` is handled the same way and matters more: a task waiting for a
sign-in must never grow an answer box on a surface a model provider reads. The
instructions forbid the GPT from asking for a password, a code or a key.

## Follow-up semantics

`sendFollowup` continues the same live task in the same provider session. The
session is found from the task's own id, on the host, by the adapter that owns
it — there is no session id anywhere in the request.

Nothing is composed around the text. Unlike `createTask`, which appends an
`expected_output` under a fixed heading, a follow-up lands mid-conversation and
an inserted heading would be words the agent reads as the user's.

Task Core remains the authority and refuses while a question is open, after a
terminal state, when the session is gone, and when a turn is already in flight.
Each becomes a bounded `not_allowed_now`.

## Which adapter runs

Task Core requires an `adapter_id` on every create and has no "pick one for me"
— correctly, because a default would mean a new adapter silently gaining every
project the day it was registered.

So the bridge reads the project's own registry entry and takes **the first
adapter that project lists**, in the order written in the host's
`task-projects.json`. That file is edited on the workstation and is never
writable through any API, so the choice is the operator's; it is deterministic,
so two identical requests cannot land on different agents; and there is no
`adapter_id` field on `createTask` for a caller to influence it.

## Artifacts: unavailable, and why

`syncTask` returns:

```json
{"artifacts_supported": false,
 "artifacts_unavailable_reason": "no_task_owned_artifact_model"}
```

**Absent rather than empty**, because "no artifacts for this task" would be a
claim Cofferdam cannot make.

Cofferdam has no task-owned artifact model. What exists is `EvidenceReference`
with `evidence_type: "artifact"` — an unverified *adapter claim* carrying a
free-form identifier, explicitly documented as "never dereferenced by Task Core
and never trusted as fact". There is no manifest, no changed-file set, no
digest, no project-root-relative path claim and no ownership proof.

Exposing artifacts on top of that would mean inventing the ownership proof at
the bridge, which is exactly the "add a path parameter" mistake this milestone
exists to avoid.

### The later PR this needs

A **Task Core artifact manifest** milestone, before any bridge artifact Action:

1. An adapter reports changed files as structured, project-root-relative
   claims, bounded in count and path length.
2. Task Core stores them against the task, with a digest and a size, and marks
   each `adapter_reported` until something observes it.
3. Task Core verifies containment at record time — resolved real path inside the
   verified project root, no symlink component — the same rule
   `projects.verify_root` already applies.
4. A code-owned deny list for secret-bearing paths (`.env`, key files, token
   files, `.git/config`, `.ssh/`), applied at record time so a denied path is
   never stored rather than filtered on read.
5. A bounded preview by `artifact_id` only — never a path — with a size cap and
   a MIME allowlist; binary returns metadata.

Only then can the bridge expose `list_task_artifacts` and
`get_artifact_preview`, and both would take an `artifact_id` the server minted.

## Idempotency and replay

GPT Actions and networks retry. Every mutation requires a `client_request_id`
(8–64 chars, `[A-Za-z0-9][A-Za-z0-9._:-]*`).

- Same id + same canonical body → the original outcome, `replayed: true`.
- Same id + different body → `409 idempotency_conflict`.
- Same id, concurrently → `409 request_in_flight`; exactly one caller wins.

`createTask` and `sendFollowup` also pass the key through to Task Core, which
has its own idempotency on both — two independent guards on one retry.
`submitChoiceAnswer`, `cancelTask` and `finishTask` have no upstream key and are
protected by state; the bridge's table turns their refusals back into a truthful
replay.

### The bridge's table is not a task store

`state/actions-bridge/idempotency.db`, one table, seven columns:
`(operation, scope, request_id) → (digest, task_id, claimed_at, settled_at)`.

It stores **no request body** — only a SHA-256 digest, NFC-normalized so the
same instruction typed on two devices compares equal. On replay the bridge
**re-reads the current state from Task Core** rather than returning a stored
response, so a caller retrying two minutes later learns where the task is now.

Rows are pruned after 24 hours. An unfinished claim becomes claimable again
after 120 seconds, because a bridge killed mid-mutation must not brick a request
id the caller cannot change.

## Limits

Requests are **refused** when over a bound, never truncated. Responses are
**truncated** and say so — nobody can retype an agent's result.

| | |
|---|---|
| Request body | 16 KiB |
| `task_text` | 6,000 chars (Task Core allows 8,000) |
| `expected_output` | 1,000 chars, counted against the same total |
| `followup_text` | 3,000 chars (Task Core allows 4,000) |
| `title` | 120 chars |
| Result text out | 6,000 chars, then `result_truncated: true` |
| Question / option label / description | 1,000 / 120 / 240 chars |
| Recent tasks | 10 default, 20 hard cap |
| Response body | 60 KiB (OpenAI's cap is 100,000 characters) |
| Upstream timeout | 20 s (OpenAI's round trip budget is 45 s) |
| Rate | 60/min burst 20; mutations 20/min burst 6 |
| Concurrency | 4 in flight, then 429 — refused, not queued |

Also refused: unknown JSON fields, non-JSON mutation bodies, content-type
mismatches, malformed Unicode, control characters in user text, oversized or
lying `Content-Length`, and header counts or sizes past the bound.

Every authenticated response carries `Cache-Control: no-store`,
`X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. There is
**no CORS header**: Actions are called server-to-server, so a permissive
`Access-Control-Allow-Origin` would grant page JavaScript a capability the
contract never needed.

## No background push

**Cofferdam cannot push a message into a ChatGPT conversation.** That is a
property of the product on the other side (D-2026-08-08-4), and the architecture
is built on it rather than around it.

The user or the GPT must trigger `syncTask` during a turn. The operator
instructions forbid the GPT from promising otherwise — no "I'll let you know
when it's done".

## Logging

One bounded line per request: a bridge-minted request id, the operationId, the
display reference, the HTTP status, the duration, an idempotent-replay boolean,
and a bounded error code.

The privacy rule is enforced by the **signature** of `log_request`, which has no
parameter that could hold task text, a question, an answer, a result, a header,
a body or a credential. The canonical task id is deliberately absent too: this
log describes traffic from a model provider, and correlating it to specific
tasks is the join a leaked log file would otherwise make possible.

uvicorn's access log is disabled for the same reason — its line carries the full
path, which includes a task id.

## Local setup

```bash
# 1. Let the daemon know about the bridge (off by default).
python -m cofferdam.workstation --enable-actions-bridge-caller
```

```bash
# 2. Generate the external Actions key. The value is never printed.
python -m cofferdam.actions_bridge --generate-key
```

```bash
# 3. Check configuration and both credentials without binding anything.
python -m cofferdam.actions_bridge --check
```

```bash
# 4. Run it, on loopback.
python -m cofferdam.actions_bridge
```

Configuration is code-owned. Defaults: bind `127.0.0.1:7108`, upstream
`http://127.0.0.1:7101`. `COFFERDAM_BRIDGE_BIND_HOST`,
`COFFERDAM_BRIDGE_BIND_PORT` and `COFFERDAM_BRIDGE_INTERNAL_BASE_URL` override
them; the base URL must be an origin with no path, query, fragment or embedded
credential, and anything else is a startup failure.

Binding off loopback needs `--host` **and** `--allow-public-bind`. One flag is
not enough on purpose.

No systemd unit and no drop-in ship in this PR.

## Preparing the OpenAPI import

[`docs/custom-gpt/openapi.yaml`](custom-gpt/openapi.yaml) is copy-paste ready
except for one line: `servers[0].url` is `https://REPLACE-ME.example.invalid`.
It stays a placeholder: the production document is *rendered* on the host that
owns the origin by `deploy/render-actions-openapi.py`, which substitutes the
server URL and verifies the result. A committed real origin would publish the
one fact an attacker cannot derive from the code, in the file whose whole
purpose is being copied into somebody else's product.

One thing that file cannot express, learned from the real editor rather than
from a validator: **a parameter declared with `$ref` is skipped**, and the
operation with it. Parameters are inlined for that reason; schema and response
`$ref`s import fine and stay shared.

[`docs/custom-gpt/INSTRUCTIONS.md`](custom-gpt/INSTRUCTIONS.md) holds the
operator instructions and fifteen worked examples.

Both files are checked by
[`tests/test_actions_bridge_contract.py`](../tests/test_actions_bridge_contract.py):
the schema validates, every route appears in it and nothing else does, the enums
match the code, and neither file contains a real hostname, token, task id,
question id or machine path.

## Official GPT Actions evidence

Verified 2026-08-09 from OpenAI's own documentation. Recorded rather than
remembered, because the plugin-era behaviour differs and guessing would put the
wrong constraint in the schema.

| Source | Retrieved | What it established |
|---|---|---|
| *GPT Actions* (introduction), developers.openai.com | 2026-08-09 | Actions are function calling over an OpenAPI schema; the auth mechanism is part of the action definition |
| *Getting started with GPT Actions*, developers.openai.com | 2026-08-09 | OpenAPI **3.1.0**; an `operationId` per operation; a `servers` base URL; schema names and descriptions drive which action the model calls |
| *Production notes on GPT Actions*, developers.openai.com | 2026-08-09 | **45 s** round-trip timeout; request and response payloads **under 100,000 characters** each; TLS 1.2+ on port 443 with a valid public certificate; calls originate from published OpenAI IP ranges; **custom headers unsupported**; text-only payloads; operation descriptions ≤ 300 chars and parameter descriptions ≤ 700; one auth type per action; ChatGPT backs off after repeated 429/500 |
| *GPT Actions authentication*, developers.openai.com | 2026-08-09 | None / API Key / OAuth; keys encrypted at rest; tokens sent as `Authorization: [Bearer/Basic] <token>` |
| *Getting started with GPT Actions* (consequential section) | 2026-08-09 | `x-openai-isConsequential: true` → always confirm, no "always allow"; `false` → "always allow" offered; absent → GET defaults false, everything else true |
| OpenAI Help Center, *Creating and editing GPTs* / *GPTs in ChatGPT* | 2026-08-09 | GPT creation and editing are **web-only**; a GPT uses **apps or actions, not both**; public GPTs with actions need a privacy-policy URL |

Two honesty notes on that table. The `x-openai-isConsequential` rules were
retrieved through documentation search rather than a direct page fetch — the
migrated page did not surface the section on fetch — and are corroborated
empirically by the 2026-08-08 probe, where ChatGPT rendered a confirmation
prompt for a POST marked `true`. The Help Center pages return 403 to direct
fetches and were read the same way.

A **private** GPT shared by link to nobody but its owner is the intended
configuration, so the privacy-policy requirement does not apply to PR1's plan.
Publishing would change that.

## Prior evidence: the 2026-08-08 mobile probe

A disposable probe outside this repository
(`~/cofferdam-spikes/gpt-actions-mobile-probe`, inspected read-only) established
that a private Custom GPT can reach a personal workstation service from the
**native iPhone ChatGPT app**, with bearer auth, including a consequential POST
requiring confirmation. Its log shows `POST /echo → 200 seq=3 id=mobile-app-1
seen=1`: one accepted mobile confirmation, exactly one server invocation.

It also established a transport problem worth carrying into Gate A: outbound TCP
**port 7844 was filtered** on this workstation's network, which `cloudflared`
needs for both QUIC and HTTP/2 with no fallback to 443. The working tunnel
needed a phone hotspot plus `--edge-ip-version 4 --protocol http2`.

Nothing from the probe is reused: not its token, not its schema, not its
architecture. It is historical evidence about the client path, and its bearer
scheme (`type: http, scheme: bearer`) is the one thing it confirmed works in the
real GPT builder.

---

## Gate A — external exposure

**Done.** A dedicated HTTPS origin, one Cloudflare Tunnel, one DNS record, the
external key entered in the GPT editor, the schema imported, and a real private
Custom GPT driving the bridge — including one approved Claude Code task end to
end. [`ACTIONS_EXPOSURE.md`](ACTIONS_EXPOSURE.md) holds the deployment, the
limitations and the rollback.

The main Cofferdam API and the PWA stayed private, and the mechanism is worth
restating because it is the whole argument: they are **not in the tunnel's
ingress**. Cloudflare cannot reach a service the ingress does not name, so this
is an absence rather than a rule that could be relaxed.

## Gate B — production Agent SDK

**Separate from Gate A, and independent of it.** Production runs the Claude Code
adapter, which supports start, follow-up, cancellation and results — enough for
most of the bridge. Structured clarifications need the Agent SDK adapter.

It must be possible to approve external exposure while keeping the Agent SDK
adapter disabled, or the reverse.

## Rollback

**That was PR1's rollback and it no longer applies.** PR2 installs units, moves
the runtime slot, creates credentials and opens a public origin. Reverting the
commit undoes none of that. The real sequence — GPT editor first, then tunnel,
DNS, bridge, keys, drop-in, and the slot only if the deployment itself is at
fault — is in
[`ACTIONS_EXPOSURE.md`](ACTIONS_EXPOSURE.md#rollback).

For a machine that only ever ran the bridge locally, the original advice still
holds:

If the bridge has been run locally, delete `secrets/actions-bridge-key`,
`secrets/actions-bridge-internal-token` and `state/actions-bridge/`.
