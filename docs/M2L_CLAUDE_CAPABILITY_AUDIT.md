# M2L — Claude coworker capability audit

What Cofferdam on the headless Acer server can actually *use*, through a currently supported
programmatic interface, from the Anthropic product surface. Not what exists in a UI.

**Access date for every source below: 2026-08-20.** Live checks ran against the Acer server
(`cofferdam-server`), Claude Code **2.1.227**, subscription-authenticated.

> **Scope.** This is a research document for M2L PR1b. It changes no code, no runtime and no
> deployment. It exists to make PR1c implementable from evidence rather than from assumption.

---

## 1. Executive conclusion

**Subscription-authenticated Claude Code on the Acer is a viable first planner backend for PR1c, and
no Anthropic API key is required.** Three properties were verified live rather than inferred:

1. **The no-tools boundary is host-enforced, not prompt-enforced.** `--tools ""` is documented as
   disabling all tools, and an adversarial probe confirmed it.
2. **Provider-side schema enforcement of the *model's own result* exists** — `--json-schema` puts a
   validated object in `structured_output`, separate from the free-text `result`.
3. **An Opus-class model is selectable on the Pro subscription** — `--model opus` resolved to
   `claude-opus-5`, `provider: firstParty`.

**The most important negative finding is a security one**, and it is not about a missing feature.
With tools disabled, the model still emitted *tool-call-shaped text* into its result. Nothing ran.
But it means:

> **Planner output is DATA, never EXECUTION.** Text that resembles a Bash command, an XML function
> call, JSON-RPC or an MCP invocation is inert text. Only a valid, closed `PlannerResult` may cross
> the planner boundary, and that schema must expose *semantic choices* — never `command`, `argv`,
> `shell`, `tool_name` or `mcp_method`.

**The second important finding is about the product surfaces.** Claude-native Routines, connectors
and Cowork are real and useful, but they are anchored to the **claude.ai account and Anthropic's
cloud**, not to a headless process on the Acer. They cannot carry Cofferdam's authority model. The
audit therefore recommends Cofferdam own provider-neutral Routine and Artifact abstractions and treat
provider features as optional adapters.

---

## 2. PR1a coverage result

`D-2026-08-20-1` (merged as `97267ad`, "docs: redefine M2L around cloud coworker planning (#72)")
covers the authoritative direction well. Verified present: provider neutrality, optional local AI,
Global/Project Mind separation, Obsidian as viewer, confirmation-gating, the proposal/accept path,
and the always-on server role.

**Three principles were missing and are added by this PR** — see §18 and the follow-on decision
`D-2026-08-20-2`:

1. Cofferdam is the **central orchestrator**; coworkers are not a peer-to-peer agent society.
2. The bounded **Project Handoff** is a semantic projection, not filesystem access.
3. **Routine/Artifact ownership**: Cofferdam owns the canonical abstraction when the concept
   participates in Cofferdam authority, persistence, evidence or scheduling.

---

## 3. Capability matrix

| Capability | Product surface | Headless on Acer? | Auth | Programmatic interface | Authority implication | **Primary status** | Evidence |
|---|---|---|---|---|---|---|---|
| Claude Code headless prompt | CLI `-p` | **Yes** | claude.ai Pro | CLI + JSON envelope | read-only when tools disabled | **HEADLESS_SUPPORTED_NOW** | live + [headless docs] |
| Disable all tools | CLI `--tools ""` | **Yes** | — | CLI flag | host-enforced boundary | **HEADLESS_SUPPORTED_NOW** | live probe |
| Opus-class model select | CLI `--model opus` | **Yes** | claude.ai Pro | CLI flag | none | **HEADLESS_SUPPORTED_NOW** | live |
| Actual model identity | JSON `modelUsage` | **Yes** | — | JSON envelope | provenance | **HEADLESS_SUPPORTED_NOW** | live |
| Schema-constrained result | CLI `--json-schema` | **Yes** | — | JSON `structured_output` | contains the planner contract | **HEADLESS_SUPPORTED_NOW** | live + docs |
| Duration / usage / cost estimate | JSON envelope | **Yes** | — | JSON fields | provenance | **HEADLESS_SUPPORTED_NOW** | live + docs |
| MCP as client (explicit config) | CLI `--mcp-config`, `--strict-mcp-config` | **Yes** | per-server | CLI flags | grants tools — must stay off for planner | **MCP_SUPPORTED** | CLI + docs |
| claude.ai **connectors** in CLI | claude.ai account | **No** | claude.ai | — | not inherited by CLI | **UI_ONLY** (for CLI use) | routines docs |
| Routine **fire** (trigger existing) | `/v1/claude_code/routines/{id}/fire` | Yes (HTTP) | per-routine bearer | REST, beta header | starts an autonomous cloud session | **API_SUPPORTED** (beta) | routines docs |
| Routine **create/update/delete** | claude.ai web / CLI `/schedule` | **No** | claude.ai | no CRUD API | — | **UI_ONLY** | routines docs |
| Routine execution locus | Anthropic cloud | n/a | — | — | runs autonomously, no approval prompts | **NOT_AVAILABLE** as Cofferdam scheduler | routines docs |
| Cowork task create/poll/cancel/retrieve | Cowork | Not established | — | none found | — | **UNKNOWN** | see §11 |
| Claude-native Artifacts create/retrieve | claude.ai / Cowork | Not established | — | none found for headless | — | **UNKNOWN → treat as UI_ONLY** | see §12 |
| Claude Projects / account Memory | claude.ai | Not established | — | none found | must not be canonical | **UNKNOWN** | see §13 |

---

## 4. Claude Code headless / planner analysis

Verified on the Acer:

```
version : 2.1.227
path    : /usr/bin/claude
```

Relevant flags present in this exact build: `--print/-p`, `--output-format {text,json,stream-json}`,
`--json-schema`, `--input-format`, `--model`, `--tools`, `--allowedTools`, `--disallowedTools`,
`--permission-mode`, `--strict-mcp-config`, `--mcp-config`, `--settings`, `--append-system-prompt`,
`--system-prompt`, `--bare`.

**Exit behaviour** (documented): 0 on success, non-zero on failure; an invalid flag reports to stderr
before the run; an in-run failure such as missing auth prints as the *result* on stdout — so PR1c
must check both exit status and envelope, not one alone. SIGTERM aborts the turn and exits **143**.

**A trap worth naming.** Without `--bare`, a `-p` session **loads the working directory's context and
connects the project's `.mcp.json` servers, and runs `.claude/settings.json` hooks**, because a `-p`
session shows neither a trust dialog nor a per-server approval prompt. A planner invoked in an
arbitrary project directory would therefore inherit that project's hooks and MCP servers.

**But `--bare` is not the fix here**: the documentation states bare mode *"doesn't use your
subscription login"* and never reads OAuth credentials or the keychain — it requires
`ANTHROPIC_API_KEY`. So on the subscription path PR1c must get isolation from
`--strict-mcp-config` plus a **controlled working directory**, not from `--bare`.

---

## 5. Subscription authentication

```json
{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty",
 "subscriptionType": "pro"}
```

No API key is present and none is needed. `modelUsage[*].provider` reports `firstParty` on every
call, which is the observable confirmation that the subscription path — not a Console key — served
the request.

**When the Anthropic API would become preferable** (none of these applies today): if PR1c needs
`--bare`-level isolation *and* subscription auth simultaneously (currently mutually exclusive); if
unattended server use hits subscription limits that credits cannot cover; or if a future planner
needs provider features exposed only on the Platform API. Until then, introducing a key would add a
credential and a billing surface for no capability gain.

---

## 6. Model selection / Opus result

`--model` accepts *"an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a model's
full name (e.g. 'claude-fable-5')"*.

Live, `--model opus` on the Pro subscription:

```
result        : 'OPUS_PROBE_OK'
modelUsage    : claude-opus-5   (canonical=claude-opus-5, provider=firstParty, ctx=1,000,000)
                claude-haiku-4-5 (secondary)
duration_ms   : 1795
```

**Requested vs actual are separately recordable**: the request carries the alias, and
`modelUsage[*].canonicalModel` reports what actually served. A secondary Haiku call appears
alongside the main model on both probes — PR1c should record the **set** of models used, not assume
one, and should attribute the planner result to the primary.

Default without `--model` resolved to `claude-sonnet-5`. **PR1c must set `--model` explicitly** so
the planner does not silently drift with the CLI default.

---

## 7. Process envelope vs planner result — the distinction that matters

These are two different objects and PR1c must not conflate them:

| | Outer **process envelope** | Inner **planner result** |
|---|---|---|
| Produced by | Claude Code | the model |
| Obtained via | `--output-format json` | `--json-schema` → `structured_output` |
| Trust | Cofferdam's own subprocess | **untrusted model output** |
| Contains | `session_id`, `duration_ms`, `ttft_ms`, `usage`, `modelUsage`, `total_cost_usd`, `is_error`, `permission_denials`, `api_error_status` | the `PlannerResult` |

**Provider-side schema enforcement is real.** Verified live with a closed schema
(`additionalProperties: false`, `enum` on `action`, required fields) against an ambiguous Turkish
intent with no project context:

```json
{"action": "ASK_USER", "confidence": 0.85,
 "summary": "User's request \"bu isi biraz duzeltelim\" ... has no antecedent ..."}
```

The object arrived in `structured_output`, valid and conforming — and the model correctly chose
`ASK_USER` rather than inventing a requirement.

Documented behaviour worth relying on: an invalid schema is a **hard error**
(`Error: --json-schema is not a valid JSON Schema`) rather than a silent fallback to text — true from
v2.1.205, and this host is 2.1.227. The `format` keyword is accepted but treated as annotation only
and **not enforced**, so PR1c must not rely on `format` for validation.

**Required host-side boundary regardless.** Provider enforcement is a useful first gate, not the
authority. PR1c must still: extract `structured_output` as **inert data**; validate with a
Cofferdam-owned strict validator; reject unknown fields, invalid `action`, wrong types, missing
required fields and any executable-looking surface; persist a parse failure **truthfully**; and never
infer an action from malformed output. No regex recovery. A failed parse is a failed planning turn.

---

## 8. Prompt-only / no-tools security proof

`--help` in this build documents `--tools`: *"Specify the list of available tools from the built-in
set. Use `""` to disable all tools, `"default"` to use all tools, or specify tool names."*

Adversarial probe — asked Claude to run `id` and read `/etc/passwd`, under
`--tools "" --permission-mode plan --strict-mcp-config --output-format json`:

```
is_error          : false
permission_denials: []
result            : "\n<xai:function_call name=\"Bash\">\n<xai:parameter name=\"command\">id</...
```

**Nothing executed.** `id` never ran; `/etc/passwd` was never read. `permission_denials` is empty
precisely *because no tool call was ever dispatched* — the tool did not exist, so the model produced
text shaped like a call instead.

### 8.1 The tool-call-shaped-text finding (permanent PR1c rule)

The boundary held. The **output** is the hazard. A naive consumer that pattern-matches for tool calls,
or that hands planner text to anything capable of execution, would manufacture authority that the
provider correctly refused to grant.

> Even if the planner returns text resembling a Bash command, an XML function invocation, JSON-RPC,
> an MCP call, or an instruction to another worker — **it remains inert text.** Only Cofferdam-owned
> code interpreting a valid closed `PlannerResult` may act, and `PlannerResult` must expose semantic
> choices (`action = PREPARE_WORKER_PROMPT`) rather than execution primitives. Worker execution stays
> a separate bounded adapter authority.

---

## 9. MCP

Claude Code is a full MCP **client**: `claude mcp add` supports stdio, HTTP and SSE transports, with
headers for auth. Config scopes are local (`claude mcp add`, stored on the machine), project
(`.mcp.json`, committed) and via `--mcp-config`.

Two facts decide PR1c:

- **`--strict-mcp-config` restricts the session to servers from `--mcp-config` only.** Combined with
  passing no `--mcp-config`, this yields a session with no MCP servers.
- **Without it, a `-p` run connects the project's `.mcp.json` automatically**, with no approval
  prompt available.

**Recommendation for PR1c: disable MCP entirely** (`--strict-mcp-config`, no `--mcp-config`). The
planner needs no tools, and all bounded context can be delivered *in the request packet*. An MCP
server is a tool surface; adding one to a role defined as tool-less would reintroduce exactly the
authority this design removes.

**Future viability is genuine, though.** A bounded Cofferdam MCP server exposing *semantic*
operations — `read_project_handoff`, `read_selected_memory`, `read_task_result`,
`propose_memory_update`, `list_relevant_artifacts` — is architecturally sound, because each is a
closed operation over Cofferdam-owned projections rather than a path or a command. It would be a
**later** decision with its own authority review, not part of PR1c.

---

## 10. Connectors

The distinction the audit set out to test is confirmed, and it matters:

> *"MCP servers you added locally in the CLI with `claude mcp add` are stored on your machine rather
> than your claude.ai account, so they do not appear in the connectors list."* — routines docs

**claude.ai connectors are account-scoped and are not inherited by a headless CLI invocation.**
Having a connector configured in the UI grants a `claude -p` run on the Acer nothing. Conversely,
CLI-added MCP servers are invisible to cloud routines unless re-added as account connectors or
committed to a repository's `.mcp.json`.

For Cofferdam this is a *simplification*: there is no ambient connector authority leaking into the
planner process, and none to audit. **Status: UI_ONLY for headless CLI purposes.**

---

## 11. Cowork

First-party material confirms Cowork exists as a surface in the Claude desktop app alongside Chat and
Claude Code, supports delegated tasks, connectors, skills/plugins, files, and **scheduled recurring
tasks**.

**What this audit did *not* establish** is a supported programmatic interface by which Cofferdam on
the Acer could create a Cowork task, supply bounded input, poll it, cancel it, or retrieve its result
and generated files. No CLI or REST surface for those operations was found in first-party
documentation within this audit's scope.

**Status: UNKNOWN**, and per the source policy that is recorded as unknown rather than converted into
support. Browser automation of the Cowork UI is explicitly **not** acceptable evidence and is not a
fallback. If a supported interface appears, Cowork becomes a candidate *worker* adapter — never the
planner, and never the authority.

---

## 12. Artifacts

Six distinct things share this word, and conflating them would be an architectural error:

| # | Thing | Nature |
|---|---|---|
| A | claude.ai Artifacts | UI-rendered interactive content; can embed MCP-connected behaviour |
| B | Cowork artifacts/files | outputs of a Cowork task |
| C | Downloadable generated documents | export of A/B |
| D | Claude Code local file outputs | ordinary files on disk — fully available headlessly |
| E | **Cofferdam Artifact** (future) | provider-neutral record Cofferdam would own |
| F | **M2K `ArtifactRecord`** | *existing evidence/audit terminology* — must not be repurposed |

**F is the one to protect.** `ArtifactRecord` already means something specific in M2K's evidence
vocabulary. A future Cofferdam Artifact concept must take a different name or explicitly extend it by
decision — not silently inherit it.

For **A/B/C**, no supported headless create/retrieve/export path was established. **Status: UNKNOWN,
treat as UI_ONLY for planning purposes.** For **D**, a headless Claude Code run that writes files
gives Cofferdam everything it needs — local bytes, path, MIME by inspection, hash, and provenance
from the run's `session_id` and `modelUsage`.

**Direction (not implemented):** if Cofferdam later needs an artifact concept it should own it —
`artifact_id`, workspace/project, task/turn provenance, producer/provider, type/MIME, title,
local/object reference, hash, `created_at`, and an *optional* provider-native reference. Provider
artifacts become a field, not the model.

---

## 13. Routines / scheduling / background work

This section changed the recommendation, so the evidence is given in full.

Claude Code **routines** are real, available on **Pro**, and run on **Anthropic-managed cloud
infrastructure** — not on the Acer. Triggers: schedule (min interval 1 hour), GitHub events, and
**API**.

**The API trigger is genuinely programmatic:**

```
POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire
Authorization: Bearer <per-routine token>
anthropic-beta: experimental-cc-routine-2026-04-01
```
returning `claude_code_session_id` and `claude_code_session_url`.

**But management is not.** *"API triggers are added to an existing routine from the web. The CLI
cannot currently create or revoke tokens."* Creation/editing is claude.ai web, Desktop, or the CLI
`/schedule` conversational command. **There is no programmatic create/update/delete API.** The
feature is in **research preview** — *"behavior, limits, and the API surface may change"* — and the
`/fire` endpoint is explicitly *"available to claude.ai users only and is not part of the Claude
Platform API surface."*

**The decisive fact is the authority model, not the API gap.** From the docs: *"Routines run
autonomously as full Claude Code cloud sessions: there is no permission-mode picker and no approval
prompts during a run. The session can run shell commands, use skills committed to the cloned
repository, and call any connectors you include."* Runs clone GitHub repositories and act as the
user's identity.

That is the opposite of Cofferdam's model — confirmation-gated, evidence-bound, locally
authoritative, no ambient tool authority. Also worth recording: a green run status *"does not mean
the task in your prompt succeeded"*, which is precisely the worker-claim-vs-machine-evidence
distinction Cofferdam already refuses to blur.

**Conclusion: Claude-native Routines must NOT become Cofferdam's scheduler.** They remain
interesting as a possible *outbound* integration later — Cofferdam firing a routine via `/fire` is a
bounded, typed action — but the scheduler, the authority and the persistence stay Cofferdam's:

```
Cofferdam Routine → Cofferdam scheduler → typed coworker job → provider adapter
  → persisted output → M2K evidence → project handoff → notification/panel
```

---

## 14. Claude Projects / Claude Memory

Not established programmatically within this audit's scope. **Status: UNKNOWN.**

The invariants hold regardless of what a later audit finds:

- **Claude Project ≠ Cofferdam Workspace**
- **Claude Memory ≠ Cofferdam Mind**

Provider-side memory may later become *optional additional context supplied to a provider*. It must
never become canonical truth, and Cofferdam must never read its own authoritative state back out of a
provider's memory.

---

## 15. Obsidian / local Markdown / Cofferdam Mind

Five options were compared:

| | Option | Verdict |
|---|---|---|
| A | Claude reads/writes the vault directly | **Rejected.** Requires file tools, i.e. abandoning the no-tools boundary, and gives a cloud model unbounded traversal of Global + Project Mind. |
| B | Cofferdam builds bounded context into the planner request | **Recommended for PR1c.** |
| C | Semantic Cofferdam MCP server | Sound, but later — see §9. |
| D | Official connector | Not available for headless CLI (§10). |
| E | Planner returns typed memory *proposals*; Cofferdam applies them | **Recommended, paired with B.** |

**Recommendation: B + E.** Cofferdam selects bounded authoritative context through the existing
Context Builder and `CloudContextProjection` egress boundary, sends it to a no-tools cloud planner,
receives a typed result containing *proposals*, and applies them according to authority class —
operational/derived history may persist automatically with provenance and a model-generated marker;
authority-bearing memory continues through the existing proposal/accept/hash-bound path.

This needs no new context system, no vault migration, no dual authority, and no filesystem access for
the model. It is also the only option that keeps `D-2026-08-20-1`'s egress rule intact: a
cloud-backed planner is external, and takes the projection like any other external model.

---

## 16. File / output handling

- **Claude Code local files (D):** fully available headlessly. Cofferdam can obtain stable identity,
  bytes, MIME, hash and provenance. This is the only file path usable today.
- **Provider-hosted artifacts (A/B/C):** no established headless retrieval — **UNKNOWN**.

PR1c does not need file output at all: the planner returns a `PlannerResult`, not files.

---

## 17. Remote control / mobile continuation

Claude Code has a cloud/web surface (`--cloud`, session URLs, `claude.ai/code`), and routine runs
produce sessions viewable and continuable in a browser.

**Recommendation: do not redesign Cofferdam around it.** The existing path —
Custom GPT / phone / future PWA → Actions Bridge → Task Core → Cofferdam — already provides
authenticated bounded remote control with Cofferdam's own authority, cursor and evidence semantics,
and it reaches *Cofferdam*, not a provider's session. Provider remote control supervises the
provider's work; Cofferdam needs to supervise Cofferdam's. They are not substitutes.

---

## 18. Security and authority consequences

1. **Planner output is data, never execution** (§8.1). Permanent.
2. **`PlannerResult` must expose semantic choices, not execution primitives.** No `command`, `argv`,
   `shell`, `tool_name`, `mcp_method`, or free-string tool identifiers.
3. **Provider schema enforcement is a gate, not the authority.** Host-side strict validation is
   mandatory; malformed output is a truthful failure.
4. **Working directory is part of the security boundary.** A `-p` run inherits the directory's hooks
   and `.mcp.json` with no approval prompt. The planner must run in a Cofferdam-controlled directory.
5. **`--bare` and subscription auth are mutually exclusive today.** Choosing the subscription path
   means isolation comes from `--strict-mcp-config` + controlled cwd instead.
6. **No ambient connector authority** reaches headless CLI (§10) — a property to preserve.
7. **Cost metadata is an estimate.** Documented as *"client-side estimates"* that *"can differ from
   your actual bill"*. Record it as `provider_reported_cost_estimate_usd`, never as billing.

---

## 19. Future capability inputs / non-binding examples

**These are inputs to future design, not committed milestones. Nothing here is a roadmap entry.**

- **Voice:** `audio → STT → transcript → lightweight intent/NLU → Cofferdam skill/action`. Candidate
  commands: play a song, find a show, submit a Cofferdam job, ask project status. Plausible role for
  a *small* local model (Turkish STT/NLU, intent parsing) — explicitly not a large general LLM.
- **Media:** "Spotify'da X aç" → intent + query extraction → media skill. Spotify/Netflix-style
  targets are examples of future skills/adapters.
- **Background server work:** scheduled project checks, recurring research, repo review, long-running
  coworker jobs, artifact generation, notification conditions, remote-supervised services.
- **Local AI:** offline fallback, cheap bounded classification, routing. Qwen3.5-4B remains installed
  and proven for bounded decisions.
- **Financial/trading:** may exist someday as a server workload. **Real-money execution requires a
  separate deterministic high-risk authority design** and is out of scope for M2L entirely.

---

## 20. Exact PR1c recommendation

**Backend:** subscription-authenticated Claude Code on the Acer. **No Anthropic API key.**

**Invocation boundary:**

```
claude -p <prompt-from-bounded-packet>
  --model opus                 # explicit; never rely on the CLI default
  --tools ""                   # host-enforced: no tools at all
  --strict-mcp-config          # no MCP servers, none inherited
  --output-format json         # process envelope
  --json-schema <PlannerResult>  # provider-side result enforcement
  --append-system-prompt <planner contract>
  # run in a Cofferdam-controlled working directory (no foreign hooks/.mcp.json)
  # stdin closed or used for the packet; 10MB cap applies
```

**Answers to the twenty PR1c questions:**

| # | Question | Answer |
|---|---|---|
| 1 | Claude Code as first backend? | **Yes** |
| 2 | Genuinely prompt-only/no-tools? | **Yes** — `--tools ""`, verified |
| 3 | Exact boundary? | above |
| 4 | Disable MCP for PR1c? | **Yes** |
| 5 | Opus-class selectable? | **Yes** — `claude-opus-5` on Pro |
| 6 | Requested + actual model recordable? | **Yes** — alias in, `modelUsage.canonicalModel` out |
| 7 | Provider-schema-constrained output? | **Yes** — `--json-schema` → `structured_output` |
| 8 | Host-side validation still required? | **Yes** — strict, closed, fail-truthful |
| 9 | Duration recordable? | **Yes** — `duration_ms`, `ttft_ms` |
| 10 | Token/usage recordable? | **Yes** — `usage`, `modelUsage` |
| 11 | Cost meaning on subscription? | **Client-side estimate**, not billing |
| 12 | New API key needed? | **No** |
| 13 | When would API be preferable? | §5 |
| 14 | Artifacts headless? | **UNKNOWN → treat UI_ONLY** |
| 15 | Cowork headless? | **UNKNOWN** |
| 16 | Routines headless? | **fire = API_SUPPORTED (beta); management UI_ONLY** |
| 17 | Connectors from headless CLI? | **No** |
| 18 | MCP for future semantic integration? | **Yes, later**, with its own authority review |
| 19 | Obsidian/Markdown integration? | **B + E** (§15) |
| 20 | Deferred from PR1c? | §21 |

---

## 21. Deferred / unknown capabilities

**Explicitly deferred from PR1c:** MCP of any kind · connectors · Cowork · Claude-native Artifacts ·
Claude-native Routines and any scheduler · Claude Projects/Memory · provider remote control · file
outputs · worker auto-dispatch · the evaluation loop · memory auto-application.

**Unknown, needing a later focused audit:** Cowork programmatic surface · Claude-native Artifact
create/retrieve/export · Claude Projects and account Memory programmatic access · whether routine
CRUD gains an API after research preview · documented plan limits for sustained unattended
subscription use on a server.

---

## Sources

All first-party; accessed **2026-08-20**.

| Title | Publisher | URL |
|---|---|---|
| Run Claude Code programmatically (headless) | Anthropic | https://code.claude.com/docs/en/headless |
| Automate work with routines | Anthropic | https://code.claude.com/docs/en/routines |
| Trigger a routine via API | Anthropic | https://platform.claude.com/docs/en/api/claude-code/routines-fire |
| Claude Code CLI reference | Anthropic | https://code.claude.com/docs/en/cli-reference |
| MCP in Claude Code | Anthropic | https://code.claude.com/docs/en/mcp |
| Schedule recurring tasks in Claude Cowork | Anthropic Help Center | https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork |
| What are artifacts and how do I use them? | Anthropic Help Center | https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them |
| Navigating the Claude desktop app | Anthropic | https://claude.com/resources/tutorials/navigating-the-claude-desktop-app |

**Live checks** (Acer, `cofferdam-server`, Claude Code 2.1.227, 2026-08-20): version/path, `claude
auth status`, `--help` flag surface, `claude mcp --help`, adversarial no-tools probe, `--model opus`
probe, `--json-schema` structured-output probe. Read-only; no runtime configuration changed and no
service stopped.

**Staleness note.** Routines are in research preview and their API surface is documented as subject
to change. The Cowork/Artifacts/Projects entries are `UNKNOWN` because first-party programmatic
documentation was not located within this audit's scope — not because absence was proven.
