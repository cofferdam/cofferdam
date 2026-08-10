# Agent Task Core

Architecture and user guide for M2F — durable tasks, a strict lifecycle, and
replaceable agent adapters.

This is the **foundation** milestone. It ships a complete task system and
deliberately no way to run a real agent in it: the Claude Code adapter is the
next milestone, and drawing this boundary before that integration exists is the
entire point.

See also: [`YOUTUBE_PLAYER.md`](YOUTUBE_PLAYER.md),
[`SPOTIFY_PLAYBACK.md`](SPOTIFY_PLAYBACK.md),
[`AUDIO_CONTROL.md`](AUDIO_CONTROL.md), and
[`MEDIA_PROVIDER_SETUP.md`](MEDIA_PROVIDER_SETUP.md) for the media and audio
surfaces, which are unaffected by this milestone.

---

## The four layers, and why they stay apart

| Layer | What it owns | Where it lives |
| --- | --- | --- |
| **1. Task Core** | identity, lifecycle, persistence, events, policy, API, cancellation and follow-up contracts | `cofferdam/workstation/tasks/` |
| **2. Agent adapters** | making something actually happen, and reporting it | `cofferdam/workstation/tasks/adapters/` |
| **3. Origin / return route** | where a task was asked for | the PWA today; a CLI, a ChatGPT app or an Opera companion later |
| **4. Resource / evidence** | what a task touched, and how well that is known | narrow here; a full audit is a later milestone |

The separation is enforced, not merely intended. A test scans Task Core for the
name of any specific integration and fails if one appears: Claude-specific
names, process parsing and CLI behaviour belong in layer 2, and the day that
adapter is written it must not need to reach into layer 1 to work.

### Manual-first, permanently

Nothing here routes, plans, decides or infers. A person chooses the project, the
adapter, the prompt, and every follow-up and cancellation. There is no model
call anywhere in Task Core — no API key, no client, no request — and **text
produced by a model is never authority for an action on this machine**. The task
system works with no model API, no natural-language routing, no autonomous
loops, no browser automation and no cloud execution, because none of those is
part of it.

---

## Task identity

Every task gets a server-generated id:

```
task_01k2y9m4qh7v3xz8b0nf6rjdw5
```

26 characters of Crockford base32 after the prefix: **48 bits of creation time**
followed by **80 bits of randomness**.

| Requirement | How |
| --- | --- |
| Globally unique on this installation | 80 random bits, checked in tests against 10 000 draws and 2 000 draws from a single millisecond |
| Opaque to the client | no structure a client can parse or predict |
| Safe as a database key and in a URL | lowercase alphanumeric, no `i`, `l`, `o` or `u` |
| **Not derived from prompt text** | `new_task_id()` takes no prompt — there is no argument one could arrive in |
| Not derived from a pid | pids are reused within hours; a task outlives its process |
| Never reused | never chosen, only generated |
| Sortable | the timestamp prefix sorts by age in an index and a listing |
| Stable across a restart | it is a stored column, not a runtime handle |

Sortability is **added to** the randomness, never taken out of it. The timestamp
reveals when a task was created, which the record already states in plain text.

Alongside it, every task carries `correlation_id`, `parent_task_id` (reserved),
`created_at`, `updated_at`, `origin`, `adapter_id`, `project_id` and
`lifecycle_revision`.

**`origin` is assigned by the server** from the authenticated request context.
There is no request field for it, and no way for a client to say how its own
request will later be attributed.

---

## Projects: where a task may run

The most dangerous field an agent task API could have is a working directory. A
client that can name a path can name `/`, `~/.ssh`, or somebody else's data, and
every later safety property would have to be re-derived from a value that
arrived over the network.

So there is no such field. A request names a **project id**; the server resolves
it.

Projects live in a host-owned file beside the M2A registries:

```
$COFFERDAM_HOME/config/task-projects.json
```

```json
{
  "projects": [
    {
      "project_id": "cofferdam",
      "display_name": "Cofferdam",
      "root": "/home/you/cofferdam",
      "adapters": ["validation"],
      "enabled": true,
      "notes": "the main repository"
    }
  ]
}
```

Validated at **load**: strict fields, bounded strings, an absolute literal path
with no `~`, no `$VAR` and no `..`. One broken entry is dropped with a reason
and never takes the others down. A project entry carrying `command`, `argv`,
`env`, `exec`, `shell` or `script` is **refused by name** — configuration says
*where*, never *what to run*.

Re-verified at **use**, immediately before each task is created:

* every path component is checked with `lstat` for a symlink, so a root reached
  through a link is refused;
* the resolved real path must equal the configured path;
* it must be a directory, and it must be enterable.

Refused: unknown projects, disabled projects, roots outside what was configured,
symlink escapes, deleted or inaccessible roots, client-supplied working
directories, relative traversal, and arbitrary home or system paths.

**Nothing is auto-registered.** A registry that scanned `~` and offered what it
found would grant access by accident; the value of this boundary is that
somebody wrote each entry down on purpose.

An empty `adapters` list means **no adapters**, not all of them — so a future
adapter never silently gains access to an existing project.

### Which adapter runs: `delegated_adapter`

`adapters` says which adapters *may* run here. It does not say which one *does*,
and that second question has a caller who cannot answer it: `createTask` on the
Actions bridge has no adapter field, on purpose, because a model provider
choosing which agent runs on somebody's workstation is the shape M2I.5 exists to
prevent.

Until M2I.5 PR3 the answer was **the first entry of `adapters`** — fine while
every delegated project permitted exactly one, and not fine at all once the two
Claude transports could both be registered. It was also not the rule it looked
like: `adapters` is sorted at load, so "first" meant *alphabetically first*, and
`claude-agent-sdk` would have silently beaten `claude-code`.

So a project may now name one:

```json
{
  "project_id": "some-project",
  "display_name": "Some project",
  "root": "/home/you/some-project",
  "adapters": ["claude-code", "claude-agent-sdk"],
  "delegated_adapter": "claude-agent-sdk",
  "enabled": true
}
```

It resolves to one of four words, published on `/api/task-projects` as
`delegation` alongside the resolved `delegated_adapter`:

| `delegation` | When | A task can start |
| --- | --- | --- |
| `ok` | one permitted adapter, or a delegated one that is permitted | yes |
| `no_adapter` | the project permits none | no |
| `ambiguous_adapter` | several permitted, none delegated | **no** |
| `delegated_adapter_unavailable` | the delegated one is not permitted here, or this build never registered it | **no** |

Three properties are worth stating plainly, because each is a thing that could
have been done the convenient way instead:

* **Ordering is never authority.** Not registry order, not sorted order. Two
  permitted adapters and no delegation is a refusal, not a coin toss.
* **One permitted adapter stays implicit.** There is nothing to choose between,
  so no existing registry has to be rewritten to keep working.
* **A delegation is a selection, never a grant.** It cannot reach an adapter the
  project does not permit or the build did not register — those fail closed, in
  the direction where no task runs.

`delegated_adapter` carries an adapter **id** and nothing else. No flag, path,
model, effort, tool list, permission mode or budget: those are execution values,
they live in the adapter's own source, and a configuration file that could carry
them is what the forbidden-field list refuses.

**The root is never published.** `/api/task-projects` returns names and ids; the
machine's directory layout stays on the machine.

---

## Task lifecycle

Eleven states. Every one is a different sentence on a phone and a different set
of things a person can do next.

| State | Meaning | Terminal |
| --- | --- | --- |
| `created` | the task exists and is durable | no |
| `queued` | accepted, not yet handed over | no |
| `starting` | being handed to the adapter | no |
| `running` | the adapter reported it started | no |
| `waiting_for_user` | paused, needs a person | no |
| `cancelling` | a stop was requested, not yet observed | no |
| `completed` | the adapter reported a result | **yes** |
| `failed` | the task itself went wrong | **yes** |
| `cancelled` | stopping was observed | **yes** |
| `interrupted` | Cofferdam restarted underneath it | **yes** |
| `recovery_required` | survived a restart, awaiting a decision | no |

`recovery_required` is non-terminal **only because** an explicit recovery
decision is architecturally possible. This milestone implements no recovery, and
no shipped adapter declares `recover_after_restart`, so in practice every
interrupted task becomes `interrupted`, which is honest and final. The branch
exists so the first adapter that genuinely can reattach is not forced to lie.

### The transition graph

```
created            → queued, failed, cancelling, cancelled, interrupted
queued             → starting, failed, cancelling, cancelled, interrupted
starting           → running, failed, cancelling, interrupted, recovery_required
running            → waiting_for_user, completed, failed, cancelling,
                     interrupted, recovery_required
waiting_for_user   → running, failed, cancelling, interrupted, recovery_required
cancelling         → cancelled, completed, failed, interrupted, recovery_required
recovery_required  → cancelling, cancelled, failed, interrupted
completed          → (nothing)
failed             → (nothing)
cancelled          → (nothing)
interrupted        → (nothing)
```

Read the failure edges first: every non-terminal state can reach `failed` and
`interrupted`, because a task can break at any moment and the daemon can be
restarted at any moment.

`cancelling → completed` is deliberate honesty rather than a loose edge: a
cancel arriving while an adapter is finishing does not un-finish it.

**Refused, with a reason:**

| Attempt | Why it is refused |
| --- | --- |
| `completed → running` | a finished task's history is not rewritten |
| `cancelled → completed` | same |
| `failed → running` | same |
| `created → completed` | a request cannot finish a task; only an adapter report can |
| `waiting_for_user → completed` | a follow-up returns a task to `running`; what happens next is the adapter's to report |
| a repeated terminal transition | completion time and result would become editable after the fact |

Every transition is validated in one place — `lifecycle.check_transition` —
called by the store **inside the transaction** that writes the row and appends
the event.

### Adapters do not get to move a task off the graph

An adapter's outcome carries a *requested* state. The core checks it, and a
request the graph does not contain is recorded as `action_rejected` while the
task stays where it is. Lifecycle event types (`task_completed`, `task_failed`,
`task_interrupted`, …) are **core-owned**: an adapter emitting one has it demoted
to ordinary output, so a completion in the history can only come from a real
transition.

---

## Persistence

SQLite, from the standard library, at:

```
$COFFERDAM_HOME/state/tasks/tasks.sqlite3
```

`store.py` for actions said it years-of-milestones ago: a capped list of recent
records is honestly served by an atomically-replaced JSON file, and *"if
task/update records outgrow it, they get SQLite"*. This is that moment, and the
reasons are specific:

* **A state change and its event must land together or not at all.** A snapshot
  saying `completed` with no completion event is a history that disagrees with
  itself. Rewriting a JSON file cannot express "these two facts are one write".
* **Event sequence numbers must be monotonic under concurrency.**
* **Restart must find non-terminal tasks cheaply**, by state.
* **Idempotency records need lookup by key**, not a scan.

Adding it costs no dependency, so the stdlib-only CI path is unchanged.

**Schema version 2** (M2I PR2) adds one table, `task_clarifications`, holding the
questions a delegated session asked and the answers given. **Schema version 3**
(M2I PR3) adds one more, `task_turns`, holding each provider turn and the result
it produced. Both changes are **additive only** — no existing column moved,
changed type or gained a constraint — so each upgrade is the schema script's own
`CREATE TABLE IF NOT EXISTS` plus a version row, and upgrading from 2 to 3 writes
no rows at all. A task that predates `task_turns` simply has none, and every
reader treats that as the ordinary answer rather than a missing record.

A question and the state change it causes are written in **one transaction**,
through the same `transition()` that already refused to write a state without its
event; a turn's outcome rides in the same write for the same reason. A task
saying `waiting_for_user` with no question, a pending question on a cancelled
task, or a task reported `ready_for_followup` with nothing recorded as having
produced the result somebody is about to read, are all disagreements between two
rows that nobody could resolve afterwards.

**A completed turn is never written again.** The update is guarded on
`completed_at IS NULL`, which is what stops a second turn, a duplicate provider
event or a late result after a cancellation from rewriting an outcome somebody
has already been shown. `tasks.final_result` does still move on — it is written
with `COALESCE` and always has been — and that is exactly why the turn table
exists.

| Property | Choice |
| --- | --- |
| Journaling | WAL — a polling reader must not block an adapter writing progress |
| Durability | `synchronous=FULL` — the write most worth not losing is the one saying a task stopped |
| Transactions | explicit `BEGIN IMMEDIATE`, so the write lock is taken before any read a decision depends on |
| Permissions | `0600` on the file, `0700` on its directory, set before the file is created |
| Schema version | recorded in `schema_meta`; a database written by a **newer** build is refused rather than migrated backwards |
| Secrets | none, ever — no tokens, no credentials, no adapter authentication state |

**Backup and corruption.** The database holds task content and no secrets, so it
can be copied with the rest of `$COFFERDAM_HOME`. If it is corrupted, SQLite
refuses to open it and the task routes answer `task_store_unavailable`
(503) — the rest of the service keeps working. Deleting the file loses task
history and nothing else; a fresh one is created on the next start. There is no
Markdown or JSON mirror that could disagree with it: human-readable summaries
may be generated *after* a durable write, never as a second authority.

---

## Restart and interruption

On start-up, Task Core reads every non-terminal task and settles it. The rule:

> **A row that says `running` is not evidence that anything is running.**

The process that was running it is gone. Nothing is resumed — not because
resuming is hard, but because resuming something whose state nobody observed is
how a task system starts lying.

* every non-terminal task becomes `interrupted` (or `recovery_required` if its
  adapter genuinely declares reattachment);
* a `task_interrupted` event is recorded with source `restart_recovery`;
* **its previous latest output is preserved** — interruption is not amnesia;
* terminal tasks are never read into this path, so a completed task cannot be
  altered by a restart;
* the interruption is visible in the PWA, worded as a restart and explicitly
  **not** as a failure.

---

## The event model

Append-only, monotonic per task. Every event carries `sequence`, `task_id`,
`created_at`, `event_type`, `actor`, `source`, `correlation_id`,
`lifecycle_revision`, a bounded payload, and optional evidence.

`actor` answers *who caused this*; `source` answers *where the claim came from*.
They are separate because they disagree in the interesting cases: a user
pressing cancel is `user`/`cofferdam`, while the task actually stopping is
`adapter`/`adapter`.

Event types: `task_created`, `task_queued`, `adapter_starting`, `task_started`,
`progress`, `meaningful_output`, `waiting_for_user`, `followup_received`,
`cancellation_requested`, `task_cancelled`, `task_completed`, `task_failed`,
`task_interrupted`, `recovery_required`, `action_rejected`.

Nothing stores an arbitrary adapter dictionary. Every field is normalized and
bounded individually, so a hostile or buggy adapter cannot make a response large.

### Waiting reasons

`clarification`, `approval`, `authentication`, `privileged_action`,
`adapter_input`, `unknown`.

The vocabulary is wider than what this milestone can do, reserved so that the
day an adapter needs to say "waiting for a password" there is already a truthful
word for it — and no temptation to reuse `clarification` for a secret.

`clarification` became answerable in **M2I PR2**, through its own pair of
authenticated routes and its own durable table. `approval` did not, and is not
going to: there is no approval table, no approval route and no field an approval
could be written into, and that absence is asserted by test rather than
documented. The two words stay two words.

---

## Secrets: reserved, not implemented

These waiting states are named for the future and **not implemented here**:

* waiting for clarification *(implemented)*
* waiting for approval
* waiting for a password
* waiting for a one-time code or authenticator
* waiting for a push approval
* waiting for a passkey
* waiting for a privileged action

**Passwords, one-time codes, passkeys and other secrets are not task content and
have no field in this system.** When they are supported, they must use a
dedicated ephemeral secure-input channel, and must **never** be sent to an
agent, written to a prompt, stored in task history, recorded in an event, or
included in an audit record. This milestone adds the vocabulary and no
mechanism, so there is no secret-input form to misuse.

---

## Adapters

A provider-neutral interface. Task Core asks; an adapter reports.

```python
capabilities()                      -> AdapterCapabilities
start(context)                      -> AdapterOutcome
send_followup(context, followup)    -> AdapterOutcome
cancel(context)                     -> AdapterOutcome
inspect(context)                    -> AdapterOutcome
recover(context)                    -> AdapterOutcome    # reserved
```

Declared capabilities: `start`, `followup`, `cancel`, `recover_after_restart`,
`structured_progress`, `final_result`, `approvals`, `authentication_waits`.

Every one defaults to `False`. An adapter gains a capability by claiming it,
never by omission. Task Core asks before offering an operation, and the PWA
renders its buttons from the same answer — so "this adapter cannot be cancelled"
is one fact with one source. An unsupported operation is a **truthful refusal**,
never a silent success.

Adapters are registered in a **code-owned table** built at start-up. There is no
path from a request to an import, a module name, a class name or a factory: a
client sends an `adapter_id`, the table is consulted, and an id that is not in it
is a refusal rather than an attempt to find one.

An adapter never receives a request body, a token, or a client-supplied path.
Its `TaskContext` is built by the core from the durable row and the
server-resolved project root.

---

## The validation task adapter

**This is not an agent. It is not AI. It runs no program and calls no model.**

It exists so the lifecycle can be validated end to end on a real host —
created through completed, waiting through follow-up, cancel, and a daemon
restart — without invoking anything real. Given a prompt it emits a fixed,
code-owned sequence of events and reaches a fixed terminal state. The prompt is
carried so the storage, bounds and privacy paths are exercised with real
content; it is never interpreted, and the only thing the adapter does with it is
note its length.

Structurally it cannot do anything else: there is no import of `subprocess`,
`os`, `socket`, `shutil`, any network client, or any filesystem write in the
file, and a test asserts that — because "the file currently has no dangerous
import" survives exactly as long as nobody adds one.

### How it is enabled

Never by default, and never by a client. One of:

```bash
python -m cofferdam.workstation --enable-validation-task-adapter
```

```json
{ "enable_validation_task_adapter": true }
```

```bash
COFFERDAM_ENABLE_VALIDATION_TASK_ADAPTER=1
```

When it is off, the adapter object is **never constructed**: it does not appear
in `/api/task-adapters`, and naming it in a request is
`task_adapter_unknown` — absent, not disabled. There is no route, field or
header that turns it on, and the flag can only be set server-side. The service
announces on every start when it is enabled.

**A default install after this merge has an empty adapter list.** That is the
honest state of a foundation milestone: a fully working task system with nothing
registered to run in it.

### Scenarios

Selected by an optional prefix on the prompt, from a fixed table:

| Prompt begins with | What happens |
| --- | --- |
| `scenario: complete` *(default)* | progress steps, then `completed` with a result |
| `scenario: wait` | `waiting_for_user`, accepts one follow-up, then `completed` |
| `scenario: fail` | `failed` with a bounded synthetic error |
| `scenario: cancel` | stays `running` until cancelled |
| `scenario: interrupt` | stays `running` so the service can be restarted under it |

Five words from a fixed tuple, matched exactly. A client cannot supply delays,
step counts, event scripts or failure messages — a validation adapter that
accepted a script would be a general-purpose event injector wearing a test's
clothes. An unrecognised prefix runs the default scenario.

---

## Cancellation

Cancellation is a **task-level request to one adapter**, not a process kill.

1. `cancellation_requested` is written transactionally and the task becomes
   `cancelling`;
2. only that task's adapter's `cancel()` is called, with that task's context;
3. the adapter reports whether it accepted;
4. the final state is what is then **observed** — `cancelled`, or `failed`, or
   `completed` if it finished first.

If the adapter refuses, the task stays `cancelling` and the refusal is recorded.
Claiming a task stopped because stopping it was requested is exactly the false
success this design refuses to produce.

There is no `pkill`, no `killall`, no `os.kill`, no signal, no process-name
matching and no `subprocess` anywhere in Task Core — asserted by a test over
every file in the package. Cancelling one task cannot affect another, because
the only identifier that leaves the method is that task's own context.

**Cancelling a terminal task is a truthful refusal** (`409
task_already_finished`) rather than a silent success, because a client that gets
"ok" learns the task changed when it did not. Repeating a cancel that is already
in flight returns the current state unchanged.

---

## Follow-up

A follow-up is accepted only when the adapter declares `followup`, the task is in
a state that can take another message, no question is open, the adapter still has
a session, no turn is already in flight, the payload is bounded and valid, and the
idempotency check passes.

It generates its own `followup_received` event and returns the task to
`running`. **A follow-up alone never completes a task** — the graph has no
`waiting_for_user → completed` edge.

The follow-up's text is stored on the task and shown in the detail view. It is
**not** copied into the event stream, not written to any log, not put in an audit
record and not copied into the turn row; the event says one arrived and how long
it was.

### Two states, two meanings (M2I PR3)

| From | What the message is | What it does to turns |
| --- | --- | --- |
| `waiting_for_user` | an answer to something the task is blocked on | **resumes** the open turn |
| `ready_for_followup` | a new instruction to a finished turn | **opens** turn N+1 |

The distinction is not a technicality. A turn that is blocked is still running,
and recording two turns for one unit of provider work would put a second
`started_at` on something that never stopped.

A task with a **pending clarification** refuses a follow-up outright
(`task_clarification_pending`) rather than superseding the question. A person who
types a new instruction while the agent is waiting to be told something specific
has not answered it, and delivering it as though they had would put words in
their mouth. The clarification answer route is the way forward.

### Whether a session is still there

`ready_for_followup` is Cofferdam's memory of an observation, not the observation
— so the adapter is asked, fresh, on every follow-up. `session_available` is the
half of the contract only the thing holding the process can answer, and it is an
*early* refusal rather than the guarantee: `send_followup` is where a message is
actually handed over and is the only place that can be authoritative.

After a restart the answer is `False` for every task, because the adapter's
dictionary of live sessions did not survive the process. The refusal is a
consequence of the world rather than a flag somebody set.

Cross-task branching and automatic sub-tasks are not implemented.

---

## Turns and results (M2I PR3)

One task, one provider session, several ordered turns. A turn is Cofferdam's unit
of evidence — one user message in, one terminal outcome out — and it is not a
transcript: there is no field here for a message list, a tool call or a provider
payload.

`turn_number` is Cofferdam's own, allocated `MAX+1` inside the transaction that
inserts the row, with the primary key as the backstop; the provider's own
ordering is kept beside it as `provider_turn_sequence` rather than instead of it.

`GET /api/tasks/{task_id}/result` returns **the latest completed turn's result**.
For a terminal task that is also the final task result, and `task_terminal`
distinguishes the two — both are fields, and the payload states `result_meaning`
in words as well. "Completed" means the turn *succeeded*, so a task whose first
turn answered and was then cancelled returns that answer with `outcome:
cancelled`: the answer is real, the cancellation is real, and the response says
both.

A terminal task with no successful turn returns its outcome and timestamp and no
invented text. A live task with nothing yet returns `task_result_not_ready` — 409
rather than 404, because the task exists.

The route is a read and only a read: no refresh, no adapter call, no state
change. Something polling it must not be able to drive an adapter by doing so.

---

## Idempotency

Task creation and follow-up accept an optional bounded `client_request_id`.

| Case | Result |
| --- | --- |
| same key, same payload | the prior task is returned; the adapter is **not** started again; `created: false`, HTTP 200 |
| same key, different payload | `409 task_idempotency_conflict` — both possible answers would be wrong |
| no key | an ordinary request |
| key older than 24 hours | pruned; a retry a day later is a new intention |

Keys are scoped to the operation and are **never authority over identity**: a
key that looks like a task id is still just a key, and the task id it maps to
was minted by the server.

The PWA mints one key per attempt and keeps it across retries and network
failures — deliberately including the failure case, because a request that may
have reached the server is exactly when retrying with the same key is what makes
finding out safe. The PWA also prevents duplicate submission locally, but the
backend remains authoritative.

---

## API

All routes require the device token. No `GET` mutates.

```
GET  /api/tasks                        list, bounded, no task content
POST /api/tasks                        create one task
GET  /api/tasks/{task_id}              one task, with its prompt
GET  /api/tasks/{task_id}/events       append-only history, paged
POST /api/tasks/{task_id}/followups    one more message to the same session
GET  /api/tasks/{task_id}/result       the latest completed turn's result
POST /api/tasks/{task_id}/finish       close a retained session on purpose
POST /api/tasks/{task_id}/cancel       ask that task's adapter to stop
GET  /api/task-adapters                registered adapters and capabilities
GET  /api/task-projects                configured projects, names only

GET  /api/tasks/{id}/clarifications                     questions being waited on
POST /api/tasks/{id}/clarifications/{qid}/answer        answer one question
```

The last two are **M2I PR2**. They carry information, never permission: the
answer body accepts exactly `answer` and `option_ids`, and there is no route in
this API — disabled, stubbed or otherwise — through which a tool approval could
be granted. See
[The Claude Agent SDK adapter](CLAUDE_AGENT_SDK_ADAPTER.md#clarification-is-not-approval).

Event paging is by **sequence cursor**, never offset:

```
GET /api/tasks/{task_id}/events?after=<sequence>&limit=<bounded>
```

`limit` is capped at 200 and a negative `after` is refused, so no expensive scan
is reachable from a query string.

Creation body — the complete client vocabulary:

```json
{
  "project_id": "cofferdam",
  "adapter_id": "validation",
  "prompt": "…",
  "client_request_id": "…",
  "title": "…"
}
```

**The client must not — and structurally cannot — submit** an executable, a
command, argv, shell text, a working directory, a filesystem path, environment
variables, an API key, an OAuth token, a return URL, a webhook or callback URL,
a process id, a systemd unit name, or an origin. These are not validated and
stripped; they are **absent**, and a body carrying one is refused with
`422 invalid_params` rather than silently filtered.

**The prompt is content for an adapter, not an OS command.**

Unsupported content types are refused (415), oversized bodies are refused before
parsing (413), and malformed JSON is refused (400).

### Refusal codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `task_unknown` | 404 | no such task |
| `task_project_unknown` | 404 | not configured on this host |
| `task_adapter_unknown` | 404 | not registered in this build |
| `task_project_disabled` | 409 | configured and turned off |
| `task_project_root_invalid` | 409 | the folder is missing, linked, or unreadable |
| `task_idempotency_conflict` | 409 | same key, different request |
| `task_illegal_transition` | 409 | the graph does not contain that move |
| `task_already_finished` | 409 | terminal |
| `task_not_waiting_for_input` | 409 | a follow-up to something that is not waiting |
| `task_result_not_ready` | 409 | the task exists and has produced nothing yet |
| `task_clarification_pending` | 409 | a question is open; answer it instead |
| `task_session_unavailable` | 409 | the provider session is gone — any result it produced is still readable |
| `task_followup_in_flight` | 409 | one message is already being delivered |
| `task_turn_limit_reached` | 409 | this conversation has gone on as long as one task may |
| `task_adapter_not_permitted_for_project` | 422 | the project does not list it |
| `task_prompt_invalid` / `task_followup_invalid` | 422 | empty, oversized, or control characters |
| `task_request_id_invalid` | 422 | malformed retry key |
| `task_followup_unsupported` / `task_cancel_unsupported` | 422 | the adapter declares no such capability |
| `task_adapter_failed` | 502 | the adapter refused or broke |
| `task_store_unavailable` | 503 | the database cannot be opened |

---

## Content and privacy

Prompts, follow-ups and results are **user content and may be sensitive**.

| Rule | How |
| --- | --- |
| Authenticated access only | every task route requires the device token |
| No prompts, follow-ups or results in daemon logs | Task Core contains no `logging`, `logger`, `print`, `stdout` or `stderr` call at all — asserted over every file |
| No task content in the browser console | `web/tasks.js` contains no `console` call |
| No task content in turn records | a turn keeps what the *provider* produced; what a person typed lives on the task, once |
| `no-store` on **every** task-content response (M2I PR4) | the detail view, the event stream, the question list and the result all carry somebody's private prompt, question or answer. `no-store` rather than `no-cache`, which still permits writing the body to disk. The adapter list is deliberately unmarked — it carries no task content, and marking everything would make the header mean nothing |
| Only drafts in browser storage, namespaced (M2I PR4) | one writer, taking a task, an operation and a string; keys are `cofferdam.taskdraft.<operation>.<task_id>`; no token, provider session id or provider payload can reach it, and every draft is removed on sign-out |
| No task content in URLs or query strings | content travels in bodies; paths carry ids |
| No task content in process argv | Task Core starts no process |
| No task content in systemd unit names | Task Core creates no unit |
| No task content in task ids | ids are timestamp + randomness |
| Bounded field lengths | prompt 8 000, follow-up 4 000, result 16 000, output 4 000 |
| Control characters rejected | tab and newline are prose; the rest are refused, not stripped |
| Valid Unicode and Turkish | ordinary text, and never a special case |

Requests exceeding a bound are **refused, not truncated** — a person can retype
a shorter prompt. Adapter output exceeding a bound is **truncated, not refused**
— nobody can retype an adapter's output.

The authenticated PWA may display task content. Full-text search is not
implemented.

### Audit

Bounded, non-secret operational records for: task created, start requested,
follow-up requested, cancellation requested, transition rejected, completed,
failed, interrupted.

Each carries the task id, adapter id, project id, operation, result and
correlation id — every one of them either an id Cofferdam minted or a word from
a closed vocabulary.

The audit function **has no parameter for content**. That is what makes "the
audit cannot carry a prompt" a property of the signature rather than a habit
every caller has to keep.

---

## Evidence

Not a complete resource audit — that is a later milestone. What exists is a
narrow, extensible foothold that gets the one thing right that would be hard to
fix later.

An event may carry bounded references: a process identity, a file path relative
to the project root, a commit hash, a PR reference, a test summary, an artifact
id, or a Cofferdam action id. Each has a type, an operation, a result, an
observed time, and a **source**:

| Source | Means |
| --- | --- |
| `adapter_reported` | the adapter said so. **A claim.** |
| `cofferdam_action` | Cofferdam did it through its own typed action path |
| `os_observed` | Cofferdam observed it on the system |
| `git_observed` | Cofferdam observed it in the repository |
| `user_reported` | a person said so |

**Adapter-reported evidence is not equivalent to observation**, and the core
enforces the distinction: whatever an adapter passes is stamped
`adapter_reported` on the way in, so an adapter cannot promote its own claim to
an observation. The PWA renders the two differently.

Cofferdam never claims complete task visibility.

---

## The PWA

A **Tasks** panel, first among the panels because it is what the product is for.

**List** — three groups: Active, Waiting for you, Finished. Each row shows a
concise status, the project, the adapter, elapsed time, and the latest
meaningful activity or the waiting reason. Terminal rows recede; a waiting task
is visually distinct and clearly actionable.

**New task** — a project selector, an adapter selector, a prompt box, and Start.
Bounded pending state, validation errors shown inline. **No working-directory
field and no command field**, because there is nothing at the other end to
receive one. The validation adapter is labelled as a validation adapter, in its
own warning-coloured block, wherever it appears.

**Detail** — task id, state, project, adapter, timestamps, what you asked, the
latest output, the final result, the failure or interruption reason, a follow-up
box when the adapter supports it, Cancel when it does, Copy result, and refresh.

**The default view is not a raw terminal log.** The event timeline is available
behind an *Advanced* disclosure — a log is where you go when the summary is not
enough, not the first thing you have to read.

**Structured questions (M2I PR4)** — when an adapter asks one, the panel reads
`GET /api/tasks/{id}/clarifications`, renders the question with its options, and
answers through `POST /api/tasks/{id}/clarifications/{qid}/answer`. A
fixed-choice question gets no free-text box, because a field whose contents the
server would refuse is not a field, and no follow-up box is offered while a
question is open. Answering is labelled on screen as **information, not
permission**: there is no approval control anywhere in the panel, and no route
for one. A question whose shape this build has not verified is labelled as
unverified rather than presented as verified.

Behaviour: bounded polling (10s, 4s while something is active), stopped while
the tab is hidden and after sign-out; **one immediate read when the tab is
foregrounded** (M2I PR4), so unlocking a phone shows the truth rather than a
ten-second-old copy — a single read, not a new timer; one action at a time with a
timeout that gives the panel back; a monotonic generation guard so an older
response can never overwrite a newer verified one; no optimistic success
anywhere. Signing out clears every task from memory **and every draft from
storage**.

**Drafts and retries (M2I PR4)** — a half-written follow-up or answer is kept in
`localStorage` under a key naming the task *and* the operation, so it survives
the page being discarded, cannot leak from one task into another, and cannot
reappear in the wrong box. Every access is guarded, because on iOS Safari the
`localStorage` property access itself can throw; a refusal costs the durability
and leaves the panel working. A restored draft is text, never a message —
returning to the app submits nothing. Request keys are scoped by operation and
task, **retained across a refusal** so a retry is recognisable as one, and
regenerated only when the words change.

Event delivery is authenticated polling with an `after=<sequence>` cursor. The
event model is designed so a future push transport can deliver the same events
without changing task semantics.

---

## Limitations of this milestone

Stated in the API payload as well as here, because a client should never have to
infer them:

* Cofferdam reports what an adapter tells it; adapter-reported evidence is not
  observation.
* An interrupted task is never resumed automatically — restarting the service
  ends it.
* Task Core runs no shell, no process and no model. What a task does is
  entirely the adapter's.
* Follow-up and cancellation are offered only where the adapter declares support.
* Secrets are never task content and have no field here.

Also not implemented: recovery from `recovery_required`, cross-task branching,
automatic sub-tasks, full-text search, a complete resource audit, push event
delivery, and any real agent.

---

## Next milestone: Claude Code Adapter

Layer 2, and nothing else. It implements `TaskAdapter` against the Claude Code
CLI: process handling, structured progress parsing, follow-up delivery, bounded
cancellation, and — if it can honestly support it — `recover_after_restart`.

Everything in this document stays where it is. If that adapter needs a change to
Task Core, that is a signal the boundary was drawn in the wrong place, and the
change belongs in a separate reviewed step rather than mixed into the
integration.
