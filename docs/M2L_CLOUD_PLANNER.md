# M2L — the cloud development planner

The provider-neutral planner role and its first backend. Implemented by PR1c-a.

Decisions this implements: [`D-2026-08-20-1`](../DECISIONS.md) (local-first is about authority, not
about where inference happens) and [`D-2026-08-20-2`](../DECISIONS.md) (Cofferdam is the central
orchestrator). Provider evidence: [`M2L_CLAUDE_CAPABILITY_AUDIT.md`](M2L_CLAUDE_CAPABILITY_AUDIT.md).

---

## The one rule

> **Planner output is DATA, never EXECUTION.**

A planner returns a typed decision. Text inside it that resembles a shell command, an XML tool call,
JSON-RPC, an MCP method or an instruction to another worker is **inert text**.
`PREPARE_WORKER_PROMPT` means *"store this for a separate bounded worker a person will confirm"* — it
does not mean *"run this"*.

This is enforced structurally, not by convention: `PlannerResult` has no field an execution primitive
fits in, and the validator refuses a forbidden key at any depth even if the provider's schema let it
through.

---

## Why the planner is not a `TaskAdapter`

`TaskAdapter` exists for something that owns a running process: it returns an `AdapterOutcome`
carrying a `requested_state`, declares `cancel` and `recover_after_restart`, and Task Core validates
its state requests against the lifecycle graph.

A planner owns nothing. Nothing to move, cancel or reattach to. Expressing it as a `TaskAdapter`
would give it a structural way to *ask for* `STATE_RUNNING`, on the interface Task Core validates
transitions from — the authority blur `D-2026-08-20-2` forbids.

**Reused instead:** the `CloudContextProjection` egress boundary, the code-owned-registry pattern,
and the convention that capability is declared rather than assumed.

---

## Implemented now

| Piece | Where |
|---|---|
| `DevelopmentPlanner` role, `PlanningTurn`, `ProviderExecution`, `PlannerCapabilities` | `planner/protocol.py` |
| `DevelopmentRequest`, `PlannerResult`, `PLANNER_RESULT_SCHEMA`, `validate_planner_result` | `planner/models.py` |
| Code-owned planner instructions | `planner/contract.py` |
| Typed failures | `planner/errors.py` |
| First provider | `planner/providers/claude_code.py` |

### Context egress

`DevelopmentRequest.projection` is typed as `CloudContextProjection` and required. A
`LocalContextPack` cannot be passed — there is no field it fits in, and the constructor rejects it.
This is the endpoint rule as a type: **rich local read authority is not permission to send.**

### Result contract

Closed vocabulary: `ASK_USER`, `PREPARE_WORKER_PROMPT`, `STOP`. Fields: `schema_version`, `action`,
`summary`, `confidence`, `worker_prompt`, `user_question`, `decision_basis`. No chain-of-thought
field — `decision_basis` is a short justification, not a reasoning transcript.

Cross-field semantics the provider schema cannot express, enforced host-side:

- `ASK_USER` requires a question and **must not** carry a worker prompt
- `PREPARE_WORKER_PROMPT` requires a non-empty prompt and must not also ask
- `STOP` carries no prompt and must explain itself

### Two gates

```
provider --json-schema   (Gate 1: the model's own output is constrained)
        ↓
structured_output        (untrusted data)
        ↓
validate_planner_result  (Gate 2: closed, strict, host-owned)
        ↓
PlannerResult            or a truthful failure
```

Gate 1 is useful and is not the authority: it is the provider's implementation of our schema, it
checks no cross-field semantics, and a future provider may not have it. **A malformed result is a
failed planning turn.** It is never repaired, never regex-recovered, and never inferred into an
action.

### Provider invocation

```
claude -p <short directive>        # request travels on stdin, never argv
  --model opus
  --tools ""                       # host-enforced: no tools at all
  --strict-mcp-config              # no MCP servers, none inherited
  --output-format json
  --json-schema <PlannerResultSchema>
  --append-system-prompt <contract>
```
run in a **code-owned working directory**.

### The working directory is a security boundary

The capability audit found that a `-p` session adopts its working directory's hooks and `.mcp.json`
**without an approval prompt**, because print mode shows no trust dialog. `--bare` would isolate
that but does not use the subscription login. So the planner runs in a Cofferdam-owned directory that
is *verified inert* on every use — refused if it has acquired a `.mcp.json`, a `CLAUDE.md` or a
`.claude/`.

### Provenance

`ProviderExecution` is kept separate from `PlannerResult`: one is Cofferdam's own subprocess
describing its run, the other is untrusted model output. It records requested and actual model, every
model the run touched, session id, duration, TTFT and tokens.

`provider_reported_cost_estimate_usd` is named at length on purpose. Anthropic documents this figure
as a **client-side estimate** that *"can differ from your actual bill"*. It is provenance, not
accounting.

---

## Durability (PR1c-b)

**Its own database.** `planner.sqlite3`, beside `tasks.sqlite3`, `workspace.sqlite3` and
`mind.sqlite3`. The planner owns no Task Core lifecycle, so planner rows do not live in Task Core's
file — that would be the semantic coupling the protocol decision exists to prevent. Own
`PLANNER_SCHEMA_VERSION`, own forward-only refusal, WAL + `synchronous=FULL`, mode `0600`.

**Lifecycle and action are separate columns.** `pending / running / succeeded / failed /
interrupted` is what the *invocation* did. `ASK_USER / PREPARE_WORKER_PROMPT / STOP` is what the
*model decided*. **A `STOP` is a successful invocation** whose result was a refusal to plan; a
provider failure is not. One column for both would make those indistinguishable later.

**Crash truth.** The request row commits *before* the provider is invoked, so a call cut off
mid-flight is visible as an abandoned row rather than as nothing. Result and provenance land in a
single `UPDATE`, so no row can read *succeeded with no action*. At startup, `pending`/`running` rows
become `interrupted` — **marked, never rerun**: re-invoking would spend a second call and assert the
first never happened, neither of which this host knows.

**The bounded packet is durable.** The exact payload the provider received is stored whole, because
a reference to mutable local sources could not later prove what the model was given. It is
projection-derived, so it carries only what was already eligible to leave. It is deliberately *not*
on the routine read model — a caller that wants the whole context has to ask for it.

**Cofferdam owns the context pipeline, not the caller.** `PlannerService.prepare_development_step`
takes semantic intent — user intent, research notes, prompt-writing guidance, authority boundary —
and builds the local pack and projects it *itself*. It does **not** accept a ready-made
`CloudContextProjection`. An earlier shape did, which quietly put the caller in charge of what left
the host: the one decision the egress boundary exists to make for them.

**Read surface.** `PlannerService.get(...)` / `.recent(...)` return a `PlannerRecord` whose
`to_dict()` is an allowlist, not a dump: `needs_user_input` and `has_prepared_prompt` are derived,
provider and context provenance are nested, and the request payload, raw envelope and session id are
absent. **No HTTP route, no bridge endpoint** — PR1c-b adds no network surface.

**A prepared prompt starts nothing.** Persisting a `PREPARE_WORKER_PROMPT` writes a string to a
column. There is no code path from it to a task, an adapter, a worker or a subprocess, and the
service exposes no `dispatch`, `create_task` or `submit`.

## Live validation (PR1c-b)

Both smokes ran against the real subscription-authenticated CLI with `--model opus`, resolving to
`claude-opus-5`:

- **`PREPARE_WORKER_PROMPT`** — messy Turkish intent, 67.8 s, a 6117-character worker prompt with
  objective, architecture, decisions-to-preserve, scope, forbidden changes, escalation, acceptance
  criteria, verification, stop conditions and expected report. Durably persisted; read-back matched.
- **`ASK_USER`** — an unresolved architecture choice. It refused to pick, asked in Turkish, and
  carried no worker prompt.
- **The full vertical slice** — active workspace → real `ContextBuilder` → non-empty
  `LocalContextPack` → real `ContextProjector` → non-empty `CloudContextProjection` → Opus →
  validated result → `planner.sqlite3` → read-back. Runs under
  `COFFERDAM_LIVE_PLANNER=1`; skipped otherwise, so the suite needs no network.

**A finding worth keeping.** The first live run returned `ASK_USER` because the fixture had no
active workspace: the pack held only the user message, and the policy correctly excluded it, so an
empty projection reached the model. The model declined to invent requirements — correct, but that is
*model* restraint. The host guarantee is narrower and is what the tests assert: Cofferdam sends the
empty projection faithfully, with its omission reasons, and fabricates nothing to fill it.

---

## The human authority gate (PR1d)

> **A model result is never rewritten into human authority.**

A `PlannerResult` says what the *planner* decided. An answer, an approval or a rejection is what a
*person* decided, and it is a separate authority-bearing record in a separate table
(`planner_authority_events`). Nothing on the human path ever updates a planner row: `action` never
becomes `approved`, a rejection never blanks `worker_prompt`, an answer never overwrites
`user_question`. *What was proposed* and *what was authorized* are two facts, and the question people
actually ask later needs both.

### Three things that are not each other

| | values |
|---|---|
| **invocation lifecycle** — what the *call* did | `pending` `running` `succeeded` `failed` `interrupted` |
| **planner action** — what the *model* decided | `ASK_USER` `PREPARE_WORKER_PROMPT` `STOP` |
| **human gate** — what the *person* decided | `awaiting_answer` `answered` · `awaiting_confirmation` `approved` `rejected` · `not_required` |

`STOP`, `failed` and `rejected` are three different sentences: a model declining to plan, a provider
breaking, and a person refusing a prepared prompt. Three words, kept.

### The gate is derived, never chosen

`derive_gate` is the only mapping from a persisted result to the decision it awaits, and no method
takes a parameter that could reinterpret one action as the other:

| persisted result | gate | permitted |
|---|---|---|
| `ASK_USER` + a question | `answer` | `answer` |
| `PREPARE_WORKER_PROMPT` + a prompt | `confirmation` | `approve`, `reject` |
| `STOP` | none — `planner_stopped` | — |
| `failed` / `interrupted` | none — `invocation_did_not_succeed` | — |
| missing its artefact | none — `result_incomplete` | — |
| a result schema this build does not speak | none — `result_schema_unsupported` | — |

### Hash-bound authority

A decision commits to the exact model output it authorized:

```
SHA256( "cofferdam.planner.authority.subject.v1"
        || lp(planner_request_id) || lp(result_schema_version)
        || lp(action) || lp(subject_bytes) )
```

Length-prefixed so fields cannot alias, action bound in so a question and a prompt cannot collide,
request id bound in so two turns that produced identical text do not share a fingerprint, and the
subject hashed **as stored** — not normalized, so a change Cofferdam considered cosmetic cannot ride
in under an approval somebody gave for the text they read. Own tag rather than
`cofferdam/hashing.py`, for the reason Mind gives: that serialization is a frozen contract belonging
to a different purpose.

And the read model publishes `binds_current_subject`, so a future dispatcher can prove **"the prompt
I am about to dispatch is exactly the prompt the user approved."** If the prompt changes, the
approval still says truthfully what was approved — it just no longer binds what is there now.

### The expected fingerprint is required

`expected_subject_fingerprint` is a **required argument on every authority operation** — answer,
approve and reject alike — with no default and no "use whatever is current" fallback. `None` and the
empty string are refused as malformed rather than read as "unspecified".

That is the difference between two properties which are easy to confuse:

| | how it is guaranteed |
|---|---|
| the stored event binds the subject that existed **when the write happened** | the fingerprint column alone |
| the person intended to authorize the subject **they were shown** | only the caller can assert it, by naming the digest it displayed |

A caller that names nothing is not saying *"I approve this text"* — it is saying *"I approve whatever
is there"*, and the gap between those is exactly where a stale view becomes an approval nobody gave.
Leaving it optional would delegate stale-view protection to every future caller forever; this module
is the canonical authority primitive, so the boundary is enforced here once.

The round trip:

```
read gate  →  display subject + subject_fingerprint
           →  submit (planner_request_id, authority action, expected_subject_fingerprint)
           →  service re-derives the current fingerprint and compares
           →  mismatch: refused as stale, nothing written
```

Three different causes produce the same refusal, and all three deserve it: the subject changed, the
caller held a digest for another request, or it held one for the other action on this request. In
every case the decision would attach to something the person did not read.

**The check runs before the terminal-decision short-circuit.** A retry does not get a free pass: an
approval resubmitted against a subject that has since moved is a stale view whether or not a decision
already exists, and short-circuiting on the existing row would answer *"already approved"* to a
caller looking at different text.

The same rule applies to an answer, in its own terms: an answer is authority for **this exact
persisted question**, not for whatever question currently belongs to that request id. And to a
rejection, which is a considered judgement of a specific prompt rather than a standing objection that
outlives what it objected to.

### Terminal, append-only, never overwritten

One decision per gate, enforced by a unique index rather than a check-then-write — of two decisions
racing, exactly one `INSERT` wins. A repeat of the *same* decision **carrying the same fingerprint**
returns the existing state truthfully (a double tap is one approval); a *contradicting* one is
refused; a *stale* one is refused before either is considered. `approve → reject`, `reject → approve`
and a second, different answer are all refusals, not updates. The table is never `UPDATE`d or
`DELETE`d from.

Correcting a decision is deliberately absent. It needs an explicit superseding-authority workflow
with its own record of who changed their mind; reusing `approve` for it would be the silent rewrite
this layer exists to prevent.

`CHECK` constraints carry the rule into the schema: `authority_action` has three legal values, and
**there is no row shape in this database that spells `dispatch`**.

### Approval does not dispatch. An answer does not replan.

`PlannerAuthorityService` is constructed with the store and a clock — no provider, no context
builder, no projector. So these are not promises kept by a careful code path; they are properties of
the object's dependencies. There is nothing there to start.

Asserted directly: approving creates no Task Core task, selects no adapter, starts no subprocess
(the test makes `subprocess` raise), writes no file, and leaves the planner row byte-identical.
Answering does not invoke the planner again and creates no new planner request.

An answer is **semantic data and stays data**. Prose, a code snippet, a URL, a line that reads exactly
like a shell command — all of it goes into a column and comes back out of one. Nothing parses it as
argv, a provider flag, an MCP method or a path.

### Provenance, stated narrowly

`actor` is a one-value vocabulary — a human decision cannot be attributed to `system`, `planner` or
`adapter`, because there is no such value to write. `source` is which trusted surface asserted it,
code-owned and closed, never read from a request body: `local_call`, `internal_test`, and
`workstation_pwa` **reserved and unwritable** until a route exists.

What a record claims is that a decision of this category arrived through this surface at this time.
It does **not** claim a particular person authenticated, because nothing on this host proves that
yet. There is no name, email, token or address field — and none one could be put in.

### Storage

`planner.sqlite3` **v1 → v2**, additive only: one new table, no column changed, no value rewritten.
A v2 database holding no decisions is a v1 database with an empty table beside it. Forward-only
refusal is unchanged and now happens **before** any DDL runs — the earlier ordering created tables
and then refused, which modified the thing it was declining to touch. Task Core's schema is
untouched at 11.

### No network surface

PR1d stays internal: planner-domain service methods only, no HTTP route, no bridge endpoint, no
public exposure. `PlannerService` is not wired into the workstation app at all — there is no
`app.state.planner` — so a device-token route would have had to first mount the whole planner
(provider, code-owned working directory, subscription CLI) into the always-on daemon. That is a much
larger authority expansion than a confirmation gate, and it is a deployment-shaped decision that
belongs to its own PR. A route added now would answer 503 to everything.

---

## The bounded development worker (PR1e)

The first PR in which Cofferdam may consume a human-approved prompt and do real development work.

### The dispatch gate

Every condition is a **hard refusal**, checked before anything is created:

```
invocation succeeded
+ action == PREPARE_WORKER_PROMPT
+ human gate == approved
+ binds_current_subject == true
+ fingerprint recomputed now == the approved fingerprint
+ project resolves and permits the worker adapter
        ↓
one bounded worker dispatch
```

The fingerprint is recomputed by the dispatch layer rather than taken from the gate — an
independent arrival at the same number by the layer that is about to *act* on it, so a future change
to how the authority layer derives its value cannot silently widen what may run.

### The signature is the security property

```python
dispatch_approved_worker_prompt(planner_request_id, *, provenance)
```

No `prompt`. No `cwd`, `repository`, `worktree`, `branch`, `command`, `argv`, `executable`, `model`,
`tools`, `mcp_config` or `adapter`. **Approve prompt A, dispatch prompt B is not something to
validate against — it is not expressible.**

### Execution ownership: Task Core

The planner was deliberately *not* a `TaskAdapter` because it owns no lifecycle. A coding worker
does — queued/running/completed/failed/cancelled, cancellation, events — so Task Core is the right
owner, and `TaskContext` already carries exactly what a worker may know: a `project_id`, a
**server-resolved** `project_root`, and the prompt. No second job system was created.

### Four facts, four owners

```
planner result   →  what the model proposed        (PR1c)
authority event  →  what the person authorized     (PR1d)
dispatch         →  what Cofferdam handed a worker (PR1e, planner.sqlite3 v3)
task             →  what the worker did            (Task Core)
```

`planner_worker_dispatches` is a *linkage*, not a copy: no state column, no result. Duplicating Task
Core's lifecycle would create two answers to "is it running" that drift apart.

### Cross-database idempotency

`planner.sqlite3` and `tasks.sqlite3` have no shared transaction, so "check → create task → link"
has a crash window. The fix is not a lock: the Task Core request key is **derived** rather than
minted — a pure function of `(planner_request_id, approved fingerprint, worker kind)` — so a retry
computes the same key, Task Core returns the task it already made, and the linkage is written for
that same task. Nothing launches twice because the second attempt never asks for a second task. The
mechanism is Task Core's own `client_request_id`, which has existed since M2F.

### Project isolation

```
planner request → workspace_id → Workspace → project_id → TaskProject → verified root
```

Every hop is host-owned configuration. The caller supplies none of it.

### Worktree and branch

Cofferdam cuts the worktree; the worker starts inside one already authorized. Under
`state/worker-worktrees/<project>/<task>` — outside every project checkout — on branch
`cofferdam/worker/<task_id>`. Model text cannot become a ref: there is no argument it could arrive
in. The canonical checkout keeps its branch, its commit and its clean status; `git worktree add`
writes bookkeeping under `.git/worktrees/`, which is how Git registers a linked worktree and is
stated rather than hidden.

### Host-level containment

**This is the part that is not a sentence in a prompt.** The `claude-code` adapter stays safe by
having no Bash. A development worker needs a shell, so the boundary moves from *which tools exist*
to *what the process can reach*: an unprivileged `bubblewrap` namespace in which the authorized
worktree is present and the rest of the machine is **absent, not denied**.

Verified against the installed CLI (2.1.221) before it was designed: the subscription login
authenticates inside it, DNS resolves, and `/home` does not exist — so `/home/nrgis/cofferdam`, the
slots, and every other project are unreachable. The worker gets a *synthetic* home holding a
credential copy and an empty CLI state file, because the real `~/.claude.json` records eight project
paths on this workstation.

If containment is unavailable the adapter reports itself unavailable and `start` refuses. **There is
no uncontained fallback.**

### Worker tool policy — no shell, and why

A separate adapter (`claude-code-worker`), not a mode of the existing one — widening the old profile
would retroactively grant a shell to every task any phone can already create.

The first version of this profile granted Bash under a command-prefix allowlist (`git`, `python3`,
`pytest`, `make`) and called those *bounded commands*. **That was wrong, and it was tested rather
than argued.** With a fake sentinel credential in the exact location the real one occupies:

| attempt | result |
|---|---|
| a shell reading `$HOME/.claude/.credentials.json` | **leaked** |
| `python3 -c` — an *allowed* prefix | **leaked** |
| project code opening a socket to a local listener | **exfiltrated**, listener logged it |

`python3`, `pytest` and `make` are **arbitrary-code launchers**. A prefix allowlist decides which
program starts and says nothing about what it does once running — an intent filter, not a boundary.
The credential must be in the namespace for the CLI to sign in, so anything there that can run code
can take it.

The shipped profile therefore grants **file tools only**, and each one is scoped to the worktree:
`Read(/work/**)`, `Write(/work/**)`, `Edit(/work/**)`, `Glob(/work/**)`, `Grep(/work/**)`. Three
layers, in order of what each is worth:

1. **No command tool** — no process, no socket. This removes the *exfiltration path* and is the
   load-bearing layer.
2. **File tools scoped to `/work`** — a positive grant, plus a `--disallowedTools
   "Read(/home/worker/**)"` deny rule behind it as defense in depth.
3. **Output scrubbing** — the final message is the one channel that leaves a namespace by design.
   It is a filter on what gets *stored*, **not** the credential boundary. The guarantee is that the
   model cannot obtain the credential through its tools; scrubbing exists for the case where
   provider or runtime metadata carries sensitive material by accident.

Only layer 1 is a statement about capability. Layer 2 is a permission rule, layer 3 is a filter, and
neither is asked to carry the boundary alone.

### Path aliases — is authorization lexical or canonical?

A project repository is model-controlled input and may already contain Git-tracked symlinks. So a
`/work/**` grant is worth exactly what the permission engine's *matching* is worth: if it matched the
string it was handed, `/work/apparently-safe → /home/worker/<credential>` would launder the
credential directory into scope.

Tested behaviourally against CLI 2.1.221, with a fake sentinel planted in the worker home, an
unrestricted control process shown to follow every alias to it first, and the alias attempted only
then:

| alias | result |
|---|---|
| `/home/worker/.claude/<sentinel>` — direct | refused |
| `/work/../home/worker/...` and `/work/./../home/worker/...` | refused |
| `/work/project-info.txt → /home/worker/...` — symlink escape | refused |
| `/work/a → /work/b → /home/worker/...` — chain | refused |
| `../home/worker/...` — relative escape | refused |
| `/proc/self/root/home/worker/...`, `/proc/self/cwd/...` | refused |
| **`/work/host-link → /etc/hostname`** — *lexically in scope* | **refused** |
| write through a dangling escape, and through an escaping *directory* link | refused |
| `/work/docs.md → README.md`, `nested/up.md → ../README.md` — safe internal | readable |

The last-but-two row is the one that settles it. `/work/host-link` is lexically inside `/work/**`; a
lexical matcher would have allowed it. **The engine authorizes the resolved target.**

Because of that, **no worktree symlink preflight ships**. The containment it would add already
exists a layer down, and sandbox code duplicating an enforced guarantee is a liability that has to be
maintained and can drift. `tests/test_worker_path_aliases.py` is what fails if a CLI upgrade ever
changes that answer — which is the event that would make a preflight necessary.

The measurement also corrected the *previous* posture. Before scoping, the grant was
"everywhere except one known directory", and under it the model read `/etc/hostname` and
`/usr/lib/os-release` on the first ask. Nothing there is a secret — the mount namespace is why — but
the policy expressed *deny one place* where the intent was *allow one place*, and those differ on
every path nobody thought to name.

### The tool list is the runtime's, not the profile's wish

`--allowedTools` grants *permission*; it does not decide which tools exist. Reading the session's own
`init` event showed a worker holding `Artifact`, `PushNotification`, `RemoteTrigger`, `CronCreate`,
`SendMessage`, `Monitor`, `DesignSync`, `ToolSearch`, `NotebookEdit`, `EnterWorktree` and the `Task*`
family — while the profile listed six file tools. Several of those reach off this machine or outlive
the dispatch.

`DENIED_TOOLS` now names the whole non-file surface, and the result is checked against the runtime
rather than reasoned about: the `init` event reports exactly `["Edit", "Glob", "Grep", "Read",
"Write"]`, with no MCP server and no slash command.

**Controller network access is not model network authority.** Cofferdam's own process must reach
Anthropic for a worker to run at all; nothing about that requires the *model* to hold a tool that
leaves the machine. The two get confused precisely because they share a process.

### Project checks run somewhere else entirely

Project code executes in a **second** bubblewrap sandbox with a deliberately different shape:

| | Claude phase | check phase |
|---|---|---|
| provider credential | present (must sign in) | **absent** |
| network | shared (must reach the API) | **disabled** (`--unshare-net`) |
| what runs | the CLI, file tools only | project code |
| who chose the command | nobody — no Bash | host-owned table, by id |

`CHECK_COMMANDS` is closed and code-owned. `make` and `npm test` are **excluded on purpose**: their
recipes are repository text, in a worktree the model just wrote to. `python3 -m unittest` is the
default because it is stdlib and needs nothing installed.

The commit is host-owned too — `worktree.commit_all`, authored as `Cofferdam Worker`. The worker
cannot commit for itself, which is the right shape rather than a limitation: a commit is durable
evidence and should not be produced by a process holding a provider credential.

A check result reports `exit_zero`, **not** `passed`. M2K owns the vocabulary of acceptance; this is
one command's exit status observed beside a dispatch.

MCP is off (`--strict-mcp-config`, no `--mcp-config`). No settings file is read
(`--setting-sources ""`), so a `.claude/settings.json` in the worktree cannot widen the profile — and
a project `CLAUDE.md` is read by the model as project input, which is the intended asymmetry with
the planner. Commits are authored as `Cofferdam Worker`, never as the operator.

### Exact prompt traceability

```
persisted worker_prompt  =  authority subject  =  dispatch digest  =  what the worker received
```

The approved prompt is appended to a code-owned execution contract **byte for byte**, after a
constant separator, so `delivered_prompt(payload)` recovers exactly what was approved.
`dispatched_prompt()` re-verifies the stored prompt against the digest recorded at dispatch and
returns `None` on mismatch — the honest answer to *"what did you send"* is never *"here is what is
there now"*.

### Worker completion is not acceptance

A finished worker is a finished **process**. "Tests passed" is a worker *claim*; what Cofferdam
observed by running Git itself is reported separately, and the read model carries
`worker_completion_is_not_acceptance`. Reconciling the two is M2K's, and is still deferred.

---

## Surviving a restart (PR1f)

PR1e ran a whole dispatch inside one synchronous `start()` and declared `recover_after_restart =
False`. A daemon restart mid-worker therefore left a task row saying `running` — which PR1e's own
doctrine calls *not evidence that anything is running* — beside a worktree, possibly some edits and
possibly a commit, with nothing able to say which.

### Recovery is not re-execution

The rule the whole PR is built on. A person approved **one** development step; a daemon that comes
back up and re-sends it is performing a second one on the strength of the first approval. So
recovery never launches a worker, re-sends a prompt, reruns a check, makes a commit, creates a
worktree, resets a working tree or deletes anything. It **classifies** and records.

That is the shape `MindService.recover_after_restart` already uses for an interrupted memory apply,
and it is deliberately the same one.

### Two halves, two owners

| | decides | how |
|---|---|---|
| Task Core | *that* a task stopped and needs a decision | `recover_after_restart()` parks it in `recovery_required` |
| Dispatch layer | *what* the decision is | `WorkerDispatchService.reconcile_after_restart()` |

No second scanner: the dispatch layer reads the task ids Task Core already parked. Task Core is not
taught what a worktree or a commit is, which is the coupling the adapter boundary exists to prevent.
The adapter's `recover()` hook stays unwired and unused — the capability flag changes the *target
state*, nothing more.

### Durable phase markers, and the ordering that makes them work

`worker/journal.py` appends one fsync'd line per phase, at a path derived from the same
`(project_id, task_id)` pair the worktree is derived from — beside the worktrees, never inside one,
because a model-writable file must not be the evidence of what the model did.

For every externally visible operation: **record the intent → perform it → record the result.**

    prepared → worker_running → worker_returned → checks_running → checks_completed
             → commit_pending → committed

A pair opened and never closed means *this may or may not have happened, go and look* — which is
exactly the question the reconciler then answers against Git. `commit_pending` is written **before**
`git commit` runs, and that single ordering is why a restart cannot produce a duplicate commit.

### Git is the authority; the journal is a lead

A `committed` entry naming a commit is never believed on its own. If Git does not have that commit
on this dispatch's branch the answer is `contradictory`, not "committed". The `prepared` entry
records the base commit, which is the one value that cannot be re-derived later — the project's
`HEAD` moves — and it makes "did this dispatch commit anything" an exact comparison instead of an
inference.

### What a restart can conclude

| outcome | what it means |
|---|---|
| `never_started` | no worktree, no journal |
| `no_work_found` | worktree at its base, nothing changed |
| `partial_work_preserved` | uncommitted edits — **kept**, not reset or committed |
| `commit_recovered` | the commit is real, is this dispatch's, and was not repeated |
| `worktree_missing` | recorded but gone. Not recreated |
| `worktree_mismatched` | somebody else's. Left untouched |
| `contradictory` | records and repository disagree |
| `undetermined` | Cofferdam could not read enough to say |

`undetermined` is a distinct answer on purpose: *could not determine* is not *nothing happened*.

### Nothing is ever reconciled to `completed`

There is no `recovery_required → completed` edge in the transition graph, and PR1f does not add one.
`completed` asserts Cofferdam observed a worker run to a successful end; after a crash it did not.
A recovered commit settles as **`interrupted` with the commit recorded beside it** — the pair of
facts a person actually needs: *the run was interrupted, and the work was not lost.* Adding that
edge would let a restart manufacture the one claim nobody was there to verify.

### Cancellation stays dominant

`settle_recovered_task` only moves a task out of `recovery_required`. A task cancelled during the
outage is terminal, is not read into the pass at all, and no amount of evidence on disk promotes it.

### Checks are reported, not repeated

A check command is code-owned and runs with no network, which makes rerunning it *probably* safe —
and "probably" is the wrong standard for something that runs unattended against a project's own test
suite, which may write files or bind a port. An unfinished check is reported as needing attention.

### Nothing is deleted

Interrupted worktrees, branches and journals are all retained; `worktree_retained` is exposed so a
later cleanup policy has something truthful to read. A preserved worktree may hold the only copy of
useful partial work.

### What the status screen can say

`DispatchView` gains `restart_occurred`, `recovery` (outcome, phase, open intents, recovered commit,
check result, changed-file count), `partial_work_preserved`, `worktree_retained`, `recovered_commit`
and a plain `sentence` — so the future panel says *"This worker had already committed before
Cofferdam restarted"* rather than `failed`. Still no host path anywhere in the routine read.

`planner.sqlite3` moves to **v4**: one additive `planner_worker_reconciliations` table, forward-only,
`INSERT OR IGNORE` so the first post-crash answer is the one kept.

---

## Cofferdam's own Claude session (PR1g)

### The outage this fixes

PR1e gave every dispatch a fresh synthetic home and **copied** the operator's
`~/.claude/.credentials.json` into it. The copy was deliberate — the CLI refreshes its own token and a
read-only bind would fail a long run midway — but it created a divergence nobody owned:

1. the worker refreshes the token **in its copy**;
2. the provider **rotates the refresh token**, invalidating the previous one;
3. the copy is discarded with the dispatch;
4. the operator's file still holds the superseded refresh token.

Observed for real during PR1f validation: one worker run succeeded, the next failed, and then
`claude -p` on the host failed too — *OAuth session expired and could not be refreshed*. Both
sessions dead, requiring an interactive re-login.

**The fix is not better copying. It is not copying.**

### What the CLI actually does, measured

Against 2.1.221, not assumed:

| question | answer |
|---|---|
| does `CLAUDE_CONFIG_DIR` relocate state? | **entirely** — credentials read from there, `.claude.json`, `projects/`, `sessions/`, `backups/` all written there |
| does anything land in `HOME`? | **nothing** |
| no credential at all | `Not logged in · Please run /login` |
| credential present but unusable | `Failed to authenticate: OAuth session expired and could not be refreshed` |
| is there a safe status command? | `claude auth status` → `{loggedIn, authMethod, apiProvider}`, no token material |

Two distinct failure texts is what makes `login_required` and `session_expired` honest answers rather
than one guess.

### The design

One durable, code-owned config root: `<state_dir>/claude-worker/config`, mode 0700, bound
**read-write** into the namespace at `/home/worker/.claude` with `CLAUDE_CONFIG_DIR` pointing at it. A
refresh lands somewhere that survives — the entire fix.

`HOME` beside it becomes a **tmpfs**: genuinely disposable, no host directory behind it. `build_home`
and `default_credentials` are deleted rather than left as traps, and `build_plan` no longer takes a
`home` parameter at all.

The interior path is under `/home/worker/` on purpose — it keeps the credential inside the path class
the file-tool deny rules already name, so PR#77's alias tests keep testing the thing they were written
to test.

### The operator's session is not touched

Nothing reads `~/.claude`. Not to copy, not to migrate, **not as a fallback**. A fallback would
silently reintroduce the rotation bug at the least observable moment — unattended, at night. An
expired worker session is a typed refusal that names itself.

### One-time bootstrap

```
python -m cofferdam.workstation.worker.auth status
python -m cofferdam.workstation.worker.auth login
```

`login` hands the terminal to the real `claude auth login` with `CLAUDE_CONFIG_DIR` pointed at the
worker's root, and gets out of the way. Cofferdam types no password, drives no browser, handles no
cookie and never reads the token that comes back. Inherited `ANTHROPIC_API_KEY` and
`CLAUDE_CODE_OAUTH_TOKEN` are dropped so an ambient key cannot authenticate as somebody else.

No HTTP route. A remote endpoint that could start a login is one that could start a login for
somebody else's account.

### Serialized, because sharing was not proven safe

Two CLI processes refreshing one token file could each rotate it, leaving the loser holding a
superseded one — the same defect indoors. An `flock` beside the config root is held for the length of
each Claude invocation. `flock` rather than a pidfile: the kernel releases it when the holder dies, so
a killed daemon does not wedge the session. It covers the Claude phase only — the checks and the
commit touch no credential and are not serialized behind it.

### `worker_auth_required` is not `implementation_failed`

A dead session now produces its own failure code, classified from the CLI's own words on both paths a
real one takes — the raw stderr path and the parsed-envelope path, which is how PR1f's validation
actually saw it (`worker_reported_error: Failed to authenticate`). A non-auth failure is **never**
relabelled this way: mislabelling in that direction sends a person to a login screen for a bug.

The session is checked **before** a worktree is cut, so an unauthenticated dispatch costs a refusal
rather than a stray branch.

### No migration

A fresh session reports `login_required` until somebody logs it in. The operator's token is not
imported, and on this host it was already invalid — importing it would have produced a session that
looked ready and failed on first use.

### The never-bind exception, and why it is narrow

`<state_dir>` is on `NEVER_BIND` because it holds the task and planner databases, every project's
worktrees and the phase journals. The worker's own Claude config root lives under it, so exactly one
leaf is exempt — recognised by its **code-owned suffix** (`claude-worker/config`), not by being the
value that was passed in. `state` itself, `state/claude-worker`, `state/tasks.sqlite3` and anything
deeper are all still refused.

This was found by the **live smoke**, not by a unit test: every unit test used a temporary state
directory that happened to sit under nothing forbidden, so the plan built fine and the first real run
against the host's own state directory was refused by the sandbox's own invariant. The regression
test now builds a plan under a real `NEVER_BIND` path.

### What the live validation showed

Three real invocations on one persistent session, with a full service-object reconstruction before
the third:

| | result |
|---|---|
| first invocation | authenticated, `["Edit","Glob","Grep","Read","Write"]` |
| second invocation | authenticated, **no login in between**, same config root |
| reconstruction → third | authenticated, **no login required** |
| operator `~/.claude` | **byte-identical throughout**, and a different file from the worker's |
| session lock | second controller refused while held; usable again once released |
| real access token | absent from every transcript and every argv |

**Refresh/rotation:** the access token did not need refreshing during the window, so no rotation was
observed — and it is not forced, because forcing one would mean manipulating a real credential. What
*was* observed is the property that matters: the CLI's own state writes (`.claude.json`, and a backup
per invocation) landed in the canonical store and were still there after the reconstruction. There is
exactly one location for a rotation to land in, and no copy anywhere for it to diverge from.

The full disposable-repository chain then ran on this session end to end — approved prompt delivered
byte-for-byte, project isolation held, credential-free checks passed, host-owned commit created,
durable read-back correct — together with the PR1f crash-window regression in the same run.

---

### Git credentials — the direction, not the implementation

Push and PR creation stay unimplemented, and the reason is now a design decision rather than an
open question: **the operator's GitHub token must not be mounted into the worker namespace.** The
preferred shape is a host-owned publisher — Cofferdam verifies the project and branch, uses a
separately held repo-scoped credential (a deploy key or GitHub App), pushes the already-authorized
branch and opens the PR. The worker never receives that credential. Building that publisher is its
own PR.

---

## The host-owned Git publisher (PR1h)

The worker finishes with a commit on a local branch and no way to share it. The publisher is what
sends it — and it is a **host** authority, not the worker's.

### Three credentials, and they stay three

| | grants | lives in | reachable by a model? |
|---|---|---|---|
| Claude provider | running a model | `state/claude-worker/config` | yes — it must be |
| **Git publisher** | pushing a branch, opening a PR | `state/git-publisher/git-credentials` | **never** |
| human approval | that a dispatch may happen at all | `planner_authority_events` | n/a |

Collapsing any two would mean a model that can edit a repository could also publish to it.

### Why not the operator's `gh` login

Inspected rather than assumed: it is a **classic** token with `gist`, `read:org`, `repo` and
`workflow`. `repo` alone is full control of every repository the account can reach; `workflow` can
rewrite CI. A publisher needs to push one branch and open one PR.

So the publisher gets a **fine-grained token** scoped to the target repository with exactly
*Contents: read and write* and *Pull requests: read and write*. Not requested: administration,
workflows, environments, deployments, secrets, org administration. A GitHub App would be narrower
still and is the right answer past a couple of repositories — it is not the right answer for the
first one, because an App registration plus a private key on this host is a larger secret-handling
surface than the thing it protects. That trade is written down so it can be revisited rather than
rediscovered.

### The secret never reaches argv, a URL, or an environment

Stored in **Git's own credentials format**, 0600, created with that mode rather than chmod-ed
afterwards. Git reads it via `-c credential.helper=store --file=<path>` — the *path* is in argv, the
secret is not. The push URL stays plain, so it can appear in an error message or a read model
harmlessly.

The GitHub API side uses `urllib` with an `Authorization` header: the secret exists only in this
process's memory, no child inherits it, and — the part that matters most — **`gh` is never invoked**,
so there is no path by which the operator's keyring could be used instead.

`HOME` is pointed at `/nonexistent` for every Git call, so a credential helper configured in the
operator's `~/.gitconfig` cannot be picked up.

### Nothing about where it publishes comes from a caller

    publish(dispatch_id)

No repository, remote, branch, base, commit, path or argv. The identity chain is:

    dispatch → project_id → host registry → verified root → that checkout's own `origin` → owner/repo

The remote is read from the repository with `git remote get-url origin`, deliberately **not** from a
registry field — a configurable remote would be a caller-editable value deciding where an
authenticated push goes. A URL that does not parse is refused, never guessed, and one carrying
embedded userinfo is refused rather than stripped.

### The gates, all before the first external write

Approved `PREPARE_WORKER_PROMPT` · approval fingerprint binds this dispatch · worker task terminal ·
worktree present and on its own derived branch · `HEAD` authored by the worker identity · branch
passes the publisher's own policy · commit unchanged since any previous publication. A publish that
will be refused costs nothing on GitHub.

### Branch policy

Only `cofferdam/worker/<task_id>`. `main`, `master`, `production`, `release`, `gh-pages` and friends
are refused twice over — by the prefix and by an explicit protected list. The refspec is
**constructed**, `refs/heads/<b>:refs/heads/<b>`, which removes a whole family of mistakes at once:
no wildcard, no `+` force prefix, no empty source (that is how a ref gets deleted), no `--tags`, no
`--mirror`. There is no force flag anywhere in the module, asserted against the string constants that
become argv rather than against the source text.

### Idempotency is a property of the operations

Push is fast-forward-only to an exact refspec, so re-running it either does nothing or fails — it can
never overwrite. The PR is looked up by exact `head`/`base` on an exact repository, never by "the
latest PR", so re-running finds the first one. GitHub's 422 *already exists* is treated as the
idempotent success it is.

Both crash windows are tested against a **real bare repository**: push-then-crash is discovered by
`ls-remote` and reconciled; PR-then-crash is linked rather than duplicated. Reconciliation **never
pushes** — a publication interrupted before its push stays interrupted and waits for an explicit
call, which is PR1f's doctrine across a network boundary.

### `publisher_auth_required` is not a worker failure

A missing or unusable publishing credential is reported as its own condition. The worker's commit is
finished and safe on a local branch; the only thing that failed is Cofferdam's ability to send it.
There is no fallback to the operator's credential — that would be the separation quietly not
existing.

### Traceability

`planner_request_id → approval fingerprint → dispatch_id → task_id → commit → branch → PR number`,
all durable in `planner_publications` (schema **v5**, additive, one row per dispatch enforced by a
unique index, with a foreign key onto the dispatch so a publication cannot dangle). No credential
column, and no host path.

Cofferdam **does not merge**. The read model says so in a field.

---

## Still deferred

**Explicit human-authorized retry.** PR1f reconciles and stops. Re-running an interrupted step is a
new authorization, not a continuation of the old one, and it needs its own gate.

Also: M2K verification of worker results · planner evaluation after a worker · next-step planning ·
consuming an ASK_USER answer into a follow-up invocation · a private device-token
confirmation/dispatch route · the "What is Cofferdam doing?" panel · Custom GPT integration · Codex
worker selection · bounded multi-step loop · A/B self-update and rollback · deployment.

**Not yet wired into the daemon.** `service.py` does not construct the planner stack and does not
register the worker adapter, so nothing in the running daemon creates a worker task or calls
`reconcile_after_restart()` — the recovery API is exercised by whoever builds the stack. Task Core
degrades correctly meanwhile: with the adapter unregistered, `recover_after_restart` finds no
adapter, so tasks settle as `interrupted` exactly as before. Whoever wires the planner in must call
`reconcile_after_restart()` at start-up, or worker tasks will park in `recovery_required` with
nothing to settle them.

The first product mode stays **prepare a prompt, then wait for the user**. There is no autonomous
continuation anywhere in this milestone — and now there is a durable record of the waiting, and of
what the user said.
