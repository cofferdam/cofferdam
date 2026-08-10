# M2I.5 Gate B — production Agent SDK delegation, validated

Sanitized record of the supervised live validation performed on 2026-08-10.

**What is deliberately not here.** No provider session identifier, no canonical task
id, no credential, no `client_request_id`, no raw prompt, no transcript, no hidden
reasoning and no SDK payload. Provider sessions are reported as a **count** and as
*same session: yes*; the identifier itself never appears in this file, in any log
this project writes, or in anything the Custom GPT can read.

---

## What was being proved

Gate A connected a private Custom GPT to a loopback Actions bridge and ran one
Claude **Code** task end to end. It could not demonstrate a structured
clarification, because the CLI transport has no channel an answer could travel back
on mid-turn. That needs the **Agent SDK** adapter, and enabling a second adapter in
production is what Gate B is.

Registering both Claude transports at once also made one existing behaviour
dangerous, and fixing that was a precondition rather than a nice-to-have — see
*Delegated adapter* below.

## Deployment

| | |
| --- | --- |
| Candidate | slot **B** |
| Rollback | slot **A**, the merged PR #33 / Gate A deployment, untouched |
| Extras installed | `workstation`, `actions-bridge`, `agent-sdk` — all repository-declared |
| Resolved SDK | **claude-agent-sdk 0.2.134**, MIT, `Requires-Python >=3.10` |
| Wheel | `claude_agent_sdk-0.2.134-py3-none-manylinux_2_17_x86_64.whl` |
| sha256 | `0cba177a0f234dcdb8dfcdd46803fbe0aadd42fbc5aebba41f65f9d00d05a420` |
| Bundled CLI in that wheel | 2.1.226 — **not used** |
| CLI actually driven | the host's own `claude`, **2.1.221** |
| Adapter enabled by | one drop-in, `deploy/dropins/30-claude-agent-sdk-adapter.conf` |

The resolved version is exactly the one `sdk.py` records as verified, so no
`REQUIRED_ATTRIBUTES` check and no profile assertion was relaxed to make this run.

The SDK's own wheel is ~286 MB installed because of the CLI it bundles. Cofferdam
passes `cli_path` for the host's copy instead: that is the binary whose sign-in the
workstation manages and the one the Claude Code adapter already drives. The SDK
warns — only warns — below CLI 2.0.0, so 2.1.221 passes silently.

## Delegated adapter — the precondition

Before this milestone the Actions bridge chose **the first adapter the project
listed**. `createTask` has no adapter field, correctly, so *something* had to
choose; taking element zero was defensible only while every delegated project
permitted exactly one adapter.

It was also not the rule it looked like. `TaskProject` **sorts** the adapter list at
load, so "first" meant *alphabetically first* — with both transports permitted,
`claude-agent-sdk` would have quietly beaten `claude-code` because `a` sorts before
`c`. Nobody would have chosen that, and nobody would have seen it happen.

`delegated_adapter` replaces it. Full semantics in
[`AGENT_TASK_CORE.md`](../AGENT_TASK_CORE.md#which-adapter-runs-delegated_adapter);
the properties Gate B depends on:

* **Ordering is authority nowhere.** Not file order, not sorted order.
* **One permitted adapter still resolves implicitly**, so no existing registry had
  to be rewritten — and `claude-sandbox` proves that in production, having gained
  no field at all.
* **Several permitted and none delegated fails closed** as `ambiguous_adapter`.
* **A delegation is a selection, never a grant**: naming an adapter the project does
  not permit, or that this build never registered, resolves to nothing.
* There is **no fallback**, including for a project payload carrying no
  `delegated_adapter` — an older daemon than the bridge — which fails closed.

Verified against the deployed registry: reversing both the project list and every
adapter array produced identical answers, and a probe project permitting both
transports with no delegation resolved to `(None, ambiguous_adapter)`.

## Projects

| project | permits | delegates to | resolves |
| --- | --- | --- | --- |
| `cofferdam` | — (validation adapter not registered) | — | `no_adapter`, never offered |
| `claude-sandbox` | `claude-code` | *(implicit)* | `claude-code` |
| `agent-sdk-sandbox` | `claude-agent-sdk` | `claude-agent-sdk` | `claude-agent-sdk` |

`agent-sdk-sandbox` is a new disposable git root under the host's validation area
with no remote. `claude-sandbox` was **not** repointed: repointing the only sandbox
there is would have destroyed the Claude Code baseline in the same edit, and would
have proved nothing about two adapters coexisting.

No filesystem path, note, delegation status, adapter configuration, SDK version,
budget or permission mode appears in any external response. `listProjects` publishes
the same five fields it always has.

## The live round trip

One task. Every mutation was initiated by a person through the real private Custom
GPT on the **native iPhone app** — nothing in this run was created, answered,
followed up or finished from the workstation.

```
listProjects   200
createTask     201   (consequential)
syncTask       200
submitChoiceAnswer 200   (consequential)
syncTask       200
sendFollowup   200   (consequential)
syncTask       200
finishTask     200   (consequential)
```

No replay, no duplicate, no conflict, and **no canonical task id in the bridge
journal** — only the display reference.

### Clarification

The agent called `AskUserQuestion` **once**. Cofferdam intercepted it in the
permission callback, published a normalized clarification, and waited.

```
question   Choose the validation marker.
options    opt1 = Atlas
           opt2 = Beacon
mode       single_choice        schema_verified = true
```

The GPT displayed the two options as **1. Atlas / 2. Beacon**. The person answered
"2". The GPT mapped that to the Cofferdam-minted `option_id` and submitted **that**:

```json
{"option_ids": ["opt2"], "text": null,
 "provenance": {"actor": "user", "source": "future_gpt_bridge",
                "outcome": "accepted", "rejection_reason": null}}
```

Three things are load-bearing in that record. The identifier is one **Cofferdam**
minted for this question, not the digit the person typed and not the label. The
free-text field is `null`, so no prose could have carried authority. And the source
is `future_gpt_bridge` — **not** `workstation_pwa`, which is what makes "a model
provider's surface answered this" a durable fact rather than an assumption.

### Results

| | |
| --- | --- |
| Turn 1 result | `Selected: Beacon` |
| Follow-up | exactly one, through `sendFollowup` |
| Turn 2 result | `Follow-up received.` |
| Total turns | 2 |
| Clarifications | 1 |
| Accepted answers | 1 |
| Provider sessions | **1** |
| Same session across start → question → answer continuation | **yes** |
| Same session across the follow-up | **yes** |
| New task for the follow-up | none |
| Finish | `finishTask`, terminal, session released, helper exited |

Helper count returned to **0** after finish; the history and both results were
retained.

### Tools, and one honest detail

The event stream contains **no** `Claude used <Tool>` line, which is what a real
tool invocation produces. It does contain one `tool_finished (detail=error)`, and
that is the designed `AskUserQuestion` path rather than an unexpected tool:
`_handle_question` **denies** the tool on every exit — the answer travels back on
Cofferdam's own channel — so the CLI records the call as an error result.

No Bash, no shell process under the daemon at any point, and **no tool approval was
ever bridged**. `can_use_tool` denies ordinary tools by construction; there is no
approval route the bridge can authenticate to.

## Sandbox integrity

Byte-identical before and after — same `HEAD`, same tree, same three blob hashes,
clean status, **zero untracked files**. The prompt told the agent to touch nothing;
the hashes are what make that a check rather than a claim.

`claude-adapter-sandbox` was likewise unchanged.

This matters because the file tools *were* in the session. The profile is fixed in
source and is not selectable per task, so `Read`, `Write`, `Edit`, `Glob` and `Grep`
were present and simply never used.

## Boundary, after the run

* Main API and PWA still private — `/`, `/app`, `/api/*` all 404 through the tunnel.
* Actions bridge still loopback-only; workstation still on the tailnet address only.
* Tunnel ingress still one hostname plus the 404 catch-all; connector never restarted.
* No Tailscale Serve or Funnel; no Remote Control host.
* All three services at `NRestarts=0` with unchanged PIDs across the entire run.
* Registry hash unchanged across the run.
* Nine operations, unchanged OpenAPI document, **no Custom GPT edit required**.

Ten `createTask` bodies carrying `adapter_id`, `adapter`, `delegated_adapter`,
`model`, `provider`, `permission_mode`, `max_turns`, `tools` or `cwd` were sent
through the real external origin. Every one was **422**, and the task count did not
move: the request schema is closed, so these are refusals rather than fields that
get ignored somewhere later.

## Mobile consequential-Action behaviour

Gate A covered reads only. Observed on the native iPhone app this time:

| Action | Confirmation prompt before the mutation |
| --- | --- |
| `createTask` | **observed**, reported explicitly |
| `submitChoiceAnswer` | **observed**, reported explicitly |
| `sendFollowup` | **observed**, reported explicitly |
| `finishTask` | **not separately reported** — see below |

The operator's summary was that the app "showed Action permission/confirmation
prompts for the consequential Actions rather than silently performing mutations",
which covers all four; the first three were also named one at a time as they
happened. `finishTask` is recorded as *not separately confirmed* rather than folded
into the blanket statement, because the difference between "they told me" and "it
follows from something they told me" is exactly the difference this file exists to
keep visible. The host cannot observe client-side confirmation at all.

No unexpected duplicate confirmation, and no mutation performed silently.
`x-openai-isConsequential` was not weakened anywhere to change this behaviour.

## Leakage

Checked, not assumed:

* canonical task id in the bridge journal — **0**
* provider session id in either journal, or in anything the GPT can read — **0**
* per-request httpx `HTTP Request:` line or upstream URL — **0**
* the option labels, the option id, either result string, or the question text in
  either journal — **0**
* credential shapes (`sk-…`, `Bearer …`, JWT, PEM) anywhere — **0**
* raw payload, transcript, reasoning or tool input in the stored task data — **none**;
  there is no column on any event class that could hold one

Two findings are recorded rather than rounded off, because both look like hits and
neither is one:

* The bridge journal contains three absolute paths — its own **start-up
  configuration dump**, printing where its key file, token file and idempotency
  database live. Pre-existing Gate A behaviour, local to the host, no values.
* The provider session id **is** stored, in `task_clarifications.provider_session_id`
  and `task_turns.provider_session_id`. That is by design: it is how Cofferdam
  proves session continuity at all. The boundary is that it never leaves the host's
  own store, and that was verified against the journals and against the live
  `syncTask` body.

## What is still unsupported, and stays unsupported

The live run exercised **one single-choice question**. It establishes nothing about
any other shape, and nothing was widened to fit:

* arbitrary free-text clarification — **unsupported**
* multiple-choice (`multiSelect: true`) — **unsupported**
* several questions in one tool input — **unsupported**
* an "Other" option with custom text — **unsupported**

Each remains covered by fixture tests that assert a **bounded unsupported result**.
A shape this build cannot defend becomes activity carrying key names and counts, and
is never fabricated into a single-choice question — the reader is allowed to fail to
understand, because an invented question would show somebody something the agent
never asked and then send their answer to a model as though it had.

`SCHEMA_VERIFIED` and `OBSERVED_SCHEMA` were **not** edited by this gate. The
observed payload matched what the reader already accepted, which is the only honest
outcome a spike can have when its finding is "you were right".

## The boundary this does not claim

The project root is a **configuration boundary the CLI is asked to respect, not a
kernel sandbox.** Cofferdam has not verified that a sufficiently determined read of
an absolute path outside the registered root is refused, and does not claim it is.

What is true and narrower: there is no shell in the session, so there is no general
execution primitive to escape with; the child environment is built by allowlist and
passed to `Popen`, so no Cofferdam credential and no provider key of any kind is in
it; and every path Cofferdam itself resolves comes from the project registry.
