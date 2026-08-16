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

### The pre-work Git baseline (M2K PR4, schema v6)

One additive table, `task_turn_git_baselines`, keyed `(task_id, turn_number)`. It
holds the Git revision and working-tree state the **host** read **before a worker
turn was allowed to begin**.

**Why it exists.** M2K PR3's observation is `git status` against the *current*
HEAD. A worker that edits files and then commits them leaves a clean tree, and
the current HEAD is the worker's own commit — so the work becomes invisible. The
missing fact was never a parser; it was a boundary recorded before the work.

**Ordering is structural, not temporal.** The capture is a committed write on the
only two paths that open a turn — `TaskService._start` and
`TaskService.send_followup` — immediately before `adapter.start` and
`adapter.send_followup`, under the dispatch lock. Nothing asks the adapter to
cooperate, and no path reaches a worker without passing through it first.

**The foreign key names `tasks`, not `task_turns`, and that follows from the
ordering rather than from convenience.** On both paths the adapter is invoked
before the turn row is written — deliberately, so that a refusal leaves no turn
behind and a follow-up is never recorded as delivered before the session took it.
A composite key into `task_turns` would therefore be unwritable at the only moment
it may be written, and it would make the honest outcome "captured, then the
adapter refused, so the turn never opened" impossible to represent. Task ownership
still travels through the cascade.

| Column | Meaning |
| --- | --- |
| `capture_state` | `captured` / `unavailable` — terminal and durable, never a retry marker |
| `head_state` | `present` / `unborn` / `unavailable` / `not_a_repository` |
| `head_revision` | the resolved object id, and **only** when `head_state` is `present` |
| `object_format` | `sha1` / `sha256`, read from the repository rather than assumed |
| `working_tree_state` | `clean` / `dirty` / `unknown` at the boundary |
| `status_coverage` | `complete` / `incomplete` / `unavailable` |
| `reason` | a closed machine code, never raw Git stderr — stderr carries host paths |
| `dispatch_state` | `captured` / `dispatch_started` / `dispatch_refused` / `turn_opened` |
| `captured_at` | audit metadata only; attribution is by turn number, never by clock |

**`dispatch_state` is a different dimension from `capture_state`, and it is what
makes the boundary crash-safe.** `capture_state` says how well the repository
could be read; `dispatch_state` says how far the worker dispatch got, which is
what decides whether the boundary may still be replaced.

It has to be durable rather than inferred. The adapter is invoked *before* the
turn row is written, so "no row in `task_turns`" describes two situations that
could not be more different: one where the worker was never called, and one where
the worker ran, **possibly committed**, and Cofferdam died before recording the
turn. Treating the second as replaceable would let a retry read the worker's own
commit and store it as the *pre-work* boundary — destroying the real one silently,
in a way every later observation would inherit.

`dispatch_started` is committed **before** the adapter call, which is what makes
`captured` mean "the adapter had provably not been reached". That is the only
replaceable state; everything past it freezes `head_revision`, `object_format`,
`head_state`, the tree state, the coverage and the capture reason.

`dispatch_refused` records that Cofferdam *learned* the dispatch produced no turn,
which is different from crashing before learning anything — but it does **not**
re-open replacement. `AdapterRefusal` is a statement of intent, not a proof about
side effects: `ClaudeCodeAdapter.send_followup` raises it when `send_turn` fails,
*after* bytes may already have reached a live worker's stdin, and the core cannot
tell that apart from an early refusal without reading an adapter's message text.

A retry after a refusal therefore reuses the same reserved turn number and
dispatches against the **same** boundary. That is the right answer as well as the
safe one: the earliest boundary for a turn number precedes every attempt at it.

`turn_opened` is written inside the same transaction as the turn row itself, so a
turn without its boundary bound to it — or a boundary bound to a turn that rolled
back — is not a state this store can produce.

**Nothing is invented.** `unborn` stores no revision and specifically not the
empty-tree object. The stored value is validated as a resolved identity — hex of
exactly the length the repository's object format produces — which refuses
`HEAD~5`, a branch name, a path and every other revspec by construction. A revspec
is a program; a boundary must be an identity. The object format is read because
Git 2.29 shipped SHA-256 repositories, so "forty hex characters" is not a rule.

**A HEAD that moves is not resolved to a guess.** HEAD is read, status is
inspected, HEAD is read again; disagreement means neither read describes the
moment, so the attempt is retried a **bounded** three times and then recorded as
explicitly unstable.

**`clean` may never rest on an `incomplete` status** — a bounded read cannot
conclude that nothing changed. Both the value type and a schema CHECK refuse it.

**Historical turns get no baseline**, and none is inferred from timestamps, the
current HEAD, the reflog or commit ancestry. `turn_baseline` answers `None`, which
means *no boundary was recorded* and never *the tree was clean*.

**The limit.** A clean host-owned snapshot does not prove only the worker changed
the repository afterwards — a person, an editor autosave or another tool can
modify the same tree concurrently. What a stored boundary supports is
machine-observed change since a recorded point, not proof of causation.

### Committed-work observations (M2K PR5, no schema change)

The boundary above, consumed. After the adapter returns and a real turn exists,
the host reads what the repository gained between the recorded revision and a
stable HEAD — the work PR3 structurally cannot see, because a worker that commits
leaves a clean tree and a clean `git status` is correct and useless.

**No schema change, and no new table.** The observation is immutable
`task_events.evidence_json` on a dedicated core-owned event type,
`committed_range_observed`. `EvidenceReference` already carried `change_kind`,
`previous_identifier` and `change_status`, and a dedicated event gets its own
`MAX_EVIDENCE_ITEMS` budget rather than competing with PR3's status evidence for
the same eight slots. Four of those slots are reserved for metadata — baseline,
target, coverage, limitation — because a truncated path list is still a usable
observation that says so, while a range whose revisions and history relation were
not recorded is not interpretable at all.

**Captured while the turn is open.** Both dispatch paths capture after the adapter
returns and the turn row exists, and before `_apply` — the only method that can
close a turn — under the same lock. The event therefore takes an ordinary sequence
inside the turn's own v5 bounds, so attribution is arithmetic rather than a later
decision. Nothing is captured after a close and attached backwards.

**Only for a turn that exists.** `dispatch_state` must say `turn_opened`. A
refused dispatch, and one that started and never produced a turn, stay the
explicitly uncertain attempts PR4 recorded; no turn is invented for either.

**A revision range is not a history.** `git diff <baseline> <target>` is a tree
comparison. Across a branch switch or a reset it reports the other history's files
as **deleted**, by a worker that deleted nothing. So `git merge-base --is-ancestor`
runs first and its three outcomes stay apart — ancestor, diverged, unreadable
object — and a divergence is recorded with no diff run at all.

**Configuration gets no vote.** `--find-renames`, `--no-ext-diff` and
`--no-textconv` are pinned on the argv: `diff.renames=false` would turn a move
into an add plus a delete, and the other two can each name a program the project
chose. The output is parsed on `--name-status`'s own grammar, whose rename records
are **source then destination** — the opposite of `git status --porcelain -z`.

**Two domains, never merged.** `worktree` is the index and working tree against
the current HEAD; `committed_range` is the revision range. A path may appear in
both — committed, then edited again — and that is two facts at two moments. Every
observation carries its domain. Within a domain a contradiction stands, because
those facts describe one instant; across domains an agreement wins, because they
describe different ones and both can be true.

**A dirty boundary may never contradict.** If PR4 recorded the tree as already
dirty, incomplete or unavailable, a change that predates the turn can be committed
inside the range and is indistinguishable from the worker's own. Such a range
shows observed change and cannot produce `operation_agreement = false` or
`claim_conflict`; the answer is `unknown` with an explicit limitation.

**`assembler_version` is 3**, and assembly still runs no Git: delete the
repository after the event is written and the bundle and its fingerprint are
unchanged. There is no new route and no bridge operation. Evaluating any of it
remains unstarted.

### Acceptance criteria (M2K PR6, schema v7)

Two additive tables carry what a turn was **required** to achieve:
`task_turn_criteria`, one row per reserved turn, and
`task_turn_criterion_items`, one row per criterion. Nothing here evaluates
anything — there is no result, verdict, met/not_met, confidence or risk column,
and no code that produces one.

**Why it exists.** Five PRs of evidence made the database good at describing what
happened and left it with nothing to compare that against: no criterion type, no
criterion set, no criterion identity, no fingerprint, no per-turn criteria
authority. "Did the work meet what was asked" had no durable question to answer.

**Pre-work, and frozen by the same event as the baseline.** A future evaluation
must refer to the criteria that were already in force **before dispatch began**;
a worker judged against criteria that changed after it started has been judged
against a moving target, and nothing the evaluator does afterwards repairs that.
So the ordering on both dispatch paths is:

```
validate → criteria snapshot → PR4 baseline → dispatch_started → adapter runs
```

Both writes commit before `dispatch_started`, which commits before the adapter
call. A test adapter opens a **separate read-only connection at its own first
instruction** and finds the snapshot row, every criterion row, a final
fingerprint, the baseline and `dispatch_state = dispatch_started` — with **no
`task_turns` row**, which is why this table's foreign key names `tasks` and not
`task_turns`, exactly as PR4's does. The criterion items *do* name the snapshot
compositely, because that parent is written one line above in the same
transaction: there is no criterion without a snapshot that owns it.

**Three states, and the last two are different facts.**

| State | Meaning |
| --- | --- |
| `present` | An immutable snapshot with at least one criterion |
| `not_provided` | Cofferdam durably recorded, before dispatch, that none were supplied |
| `legacy_unknown` | **No row.** The turn predates schema v7, or its dispatch crashed before the snapshot was written |

`legacy_unknown` is not writable — the schema's CHECK refuses it — because a
value meaning "nobody recorded anything" must not be something a writer can
record. A missing row is never read as `not_provided`, and `not_provided` is
never an empty criterion set that automatically succeeds.

**The vocabulary is small and closed.** Two kinds:

* `evidence` — a structured predicate over a project-relative path:
  `path_changed`, `path_operation` with `created` / `modified` / `deleted`, or
  `rename` with a destination. Every one is a question the *already stored*
  claim, artifact, worktree and committed-range rows can decide, with no new
  capture. `renamed` is deliberately not an operation: a rename is a two-path
  fact and `operation` carries one path.
* `manual` — a bounded description of something deterministic evidence cannot
  decide. It means **undecidable by machine**: not failed, not passed. A future
  evaluator returns it as unverified, and no model reads the description.

**No commands, by refusal rather than omission.** There is no shell string,
argv, script, test command, executable path, `check_id` or expression language,
and those field names are refused *by name* with their own reason code rather
than swept up as unknown. A criterion carrying a command is dormant execution
authority waiting for a runner. When host-owned named checks with literal argv
exist, a future kind may name one.

**Bounds refuse; they do not truncate.** At most 32 criteria and 500 characters
of description, and an over-bound submission is refused before dispatch with no
snapshot written at all. This is the one place in M2K where truncation would be
wrong in a way it is not wrong elsewhere: a bounded *observation* is honestly
`incomplete`, but a bounded *requirement set* reads afterwards as the complete
list of things the work had to do. Paths go through the same
`normalize_claim_path` and sensitive-name deny list that claims and artifacts
use, and a path is refused rather than rewritten into a safe-looking one.

**Identity is the server's.** `snapshot_id` and `criterion_id` are minted by the
store, from the same construction as `task_id`; a submitted one is refused. The
**criteria fingerprint** is a domain-tagged, length-prefixed SHA-256 over the
stored criterion facts in `ordinal` order — deterministic, stable across a
restart, and independent of row ids, insertion order, absolute paths, provider or
session ids and every clock. It deliberately excludes the task and the turn,
unlike `input_fingerprint`: it identifies *what was asked for*, so two turns given
the same requirements share it while each keeps its own snapshot id.

**Retry is conservative, mirroring PR4.** Only `dispatch_state = captured`
permits replacement. `AdapterRefusal` records `dispatch_refused` and does not
re-open it — a refusal is a statement of intent, not a proof about side effects —
so a retry of the same reserved turn dispatches against exactly the snapshot the
first attempt did. A genuinely new follow-up turn may receive a new snapshot; a
message that merely *resumes* a turn is refused if it carries criteria, because
that turn's snapshot is already frozen.

**Where criteria come from.** An internal, keyword-only `TaskService` parameter
on `create_task` and `send_followup`. **No route passes it**, the `/api/tasks`
body allowlist is unchanged, and there is no criteria route or bridge Action.
They may never come from an `AdapterOutcome` — there is no field — from a
worker's prose, from a provider session, or from evidence itself. Evidence and
requirements must come from different places, or a worker has both done the work
and set the bar.

**What a future evaluator will bind.** A deterministic `EvaluationRecord` is
expected to reference `task_id`, `turn_number`, the criteria snapshot
identity/fingerprint, `assembler_version` and the EvidenceBundle's
`input_fingerprint` — which is why both identities are frozen before the worker
starts. Its results are expected to need something equivalent to `met`,
`not_met` and `unverified`, and the doctrine is that **evidence limitations map
to `unverified`, never to `not_met`**: `legacy_unknown` criteria, incomplete
observations, incomplete claims, a dirty committed-range boundary, diverged
history and unavailable Git evidence all land there. A `claim_conflict` is not a
task-failure verdict either. **None of that is implemented.**

### Deterministic criterion evaluation (M2K PR7, schema v8)

Two additive tables answer, for each criterion, whether the stored machine
evidence for that turn satisfies it: `task_turn_evaluations`, one row per closed
turn per evaluator version, and `task_turn_criterion_results`.

**Three values, and no fourth.**

| Result | Meaning |
| --- | --- |
| `met` | The stored machine evidence satisfies the criterion |
| `not_met` | The evidence is complete enough to rule it out, and rules it out |
| `unverified` | The evidence cannot decide |

**It is not a task verdict**, and there is no column one could be written into.
No pass, no fail, no aggregate, no score, no confidence, no risk. Six
equivalences are forbidden: `not_met` is not "the task failed", `met` is not "the
task passed", `claim_conflict` is neither, "no criteria" is not success, and
incomplete evidence is not `not_met`.

**When it runs.** A turn's evidence window becomes final when
`closed_through_event_sequence` is written — the same transaction as the turn's
`completed_at`. Evaluation runs strictly after that, and in a *separate*
transaction, so a failure to write a judgement cannot roll back a task's
lifecycle. The gap that leaves — a turn closed with no judgement — is repaired by
the same function at start-up, whose query excludes anything already evaluated.
Restarting ten times produces one record. Nothing runs on a read.

**Why a table rather than an event.** PR5 wrote its observation into
`task_events.evidence_json` because it was captured while the turn was still
open, so it took an ordinary sequence inside the turn's v5 bounds. An evaluation
comes after the close; an event appended then would sit above the closed bound
and belong to no turn, and moving the bound is the rewrite bounds exist to
prevent.

**The foreign key names `task_turns`**, unlike PR4's baseline and PR6's criteria.
Those must be durable *before* the turn row exists, so they name `tasks`. An
evaluation exists only for a turn that has closed, so naming the turn makes an
evaluation of a turn that never happened unrepresentable. It binds the exact
criteria snapshot row too.

**`EVALUATOR_VERSION = 1`**, distinct from `SCHEMA_VERSION`, `ASSEMBLER_VERSION`
and the criteria model version, and part of the uniqueness constraint — so a
future version 2 records its own answer without rewriting version 1's.

**What `path_changed` actually means.**

> the turn produced a machine-observed **resulting repository change** for this
> semantic path, as seen at the post-worker observation boundary.

Not "the file was touched at some instant during execution". Cofferdam observes a
boundary, not a process: a worker that edits a file and then reverts it leaves no
resulting change and this build cannot see that it ever happened.

**Machine evidence is the only authority.** The evaluator does not read `claims`,
`ingestion` or `relationships` at all — a structural fact, asserted by scanning
its syntax tree for those attribute names. A claim cannot satisfy a criterion, a
claim's absence cannot fail one, and **incomplete claim ingestion downgrades
nothing**: claims are not the truth source for these predicates, so their
completeness is not a gate on them.

**Closure is predicate-specific and asymmetric.** One attributable observation
can prove a change happened; proving it did *not* needs every domain it could
have appeared in to have been read completely — both, because a committed change
is invisible to `git status` and an uncommitted one is invisible to the range.
Absence in a domain counts only when there is positive evidence the domain was
examined: an observation in it, or an explicit clean-tree statement.
`machine_observations_complete` is **true when nobody looked**, so it is not
sufficient on its own.

**Boundary quality gates positives _and_ negatives.** PR4 persists a **coarse**
boundary — one word for the whole tree, with no record of which paths were dirty
— so a dirty tree does *not* give a path nowhere to hide. Consider `foo.py` at
HEAD revision `A`, dirty at `B` when the turn began, restored by the worker to
`A`: nothing is committed, the tree now matches HEAD, and the resulting effect
the worker really did produce is invisible. Absence after a dirty boundary
therefore cannot distinguish "never touched" from "was dirty and put back".

So for the v1 path predicates only a `clean_complete` boundary permits either
conclusion:

| Pre-work boundary | `met` | `not_met` |
| --- | --- | --- |
| `clean_complete` | possible | possible |
| `dirty` | blocked | blocked |
| `incomplete` | blocked | blocked |
| `unavailable` | blocked | blocked |

This is a limitation of **evidence resolution**, not a statement about the work
and never a task failure. Closing it would need path-level pre-work state — new
evidence architecture, deliberately not attempted here — and a worker's claims
cannot repair it.

**Domains are never collapsed.** `created` in the committed range and `modified`
in the working tree are two true statements about two moments, so
`path_operation(P, created)` and `path_operation(P, modified)` are both met. A
rename is met only on an explicit machine rename record carrying both endpoints,
and is **never** inferred from a created plus a deleted.

**`manual` is always `unverified`.** The description is never inspected, never
parsed and never shown to a model. A capability v1 cannot decide — tests pass,
build succeeds, file contains text — is `unverified` with an
unsupported-capability reason. Nothing is executed to answer it.

**`legacy_unknown` produces no record.** A turn that predates criteria
persistence was never asked a question, and a zero-result row would assert
Cofferdam had checked something it never had. **`not_provided` produces a record
with zero results and no aggregate**, which the schema enforces so it can never
be totalled up as a pass.

There is no route, no request field and no bridge Action for any of this.

### The assessment surface (M2K PR8, no schema change)

One private, turn-qualified, read-only route publishes what PR6 and PR7 already
froze:

```
GET /api/tasks/{task_id}/turns/{turn_number}/assessment
```

**It computes nothing.** No evaluator runs, no record is created, no criteria are
touched, no event is appended and no task lifecycle moves. Reading it a thousand
times leaves the database byte-identical, which is asserted by poisoning the
writers and by comparing every table before and after.

**One route rather than two**, because criteria and evaluation are one audit
question and a reader needs both or neither. `TaskStore.turn_assessment_inputs`
reads turn state, criteria and evaluation under **one hold of the store lock**, so
a turn closing mid-request cannot produce a response pairing criteria from before
that commit with an evaluation from after.

**`require_token`, not `require_task_caller`.** The latter accepts the Actions
bridge credential — that is what makes the bridge's ten task routes work. An
assessment is Cofferdam's judgement about somebody's work measured against what
they asked for, so the bridge is refused here exactly as it is on the evidence
route, and refused because `require_token` has never heard of that credential
rather than because a check rejects it.

> **This is an intentional security boundary, not an inconsistency to tidy away**
> (D-2026-08-16-1). Ten neighbouring task routes use `require_task_caller`, so a
> refactor "unifying" them for consistency looks harmless and would silently widen
> the assessment surface to the Custom GPT caller class. Do not change this
> dependency without a scope decision of its own. The tests assert both halves —
> the bridge credential is refused here **and** still accepted where it belongs —
> so the refusal can never be mistaken for a broken credential.

**Three criteria states**, published as closed words:

| State | Meaning |
| --- | --- |
| `present` | A snapshot with at least one criterion |
| `not_provided` | Recorded before dispatch that none were supplied — **not** a pass |
| `legacy_unknown` | The turn predates criteria persistence; no snapshot is fabricated |

**Four evaluation states**, so `null` never has to be interpreted:

| State | Meaning |
| --- | --- |
| `recorded` | An EvaluationRecord exists for this evaluator version |
| `criteria_legacy_unknown` | There was never a question to answer |
| `turn_not_closed` | The turn is still running; evaluation is post-close by design |
| `not_recorded` | A closed criteria-bearing turn has no record — worth noticing, and **not** a pass, a skip, or an `unverified` result |

The word *pending* is deliberately unused: it invites polling and implies a
record is owed.

**The serializer is a whitelist, structurally.** Every published key is written
out literally. There is no `asdict`, no `vars`, no `__dict__` and no loop over
`__dataclass_fields__` anywhere in the module — those publish whatever a
dataclass gains next — and the view functions refuse the wrong type rather than
duck-typing, so a dict cannot arrive dressed as a stored record.

**No aggregate.** No overall result, pass, fail, success, score, percentage,
`all_met`, confidence or risk, in the response, the serializer or the UI. A list
of per-criterion results is not a verdict on a task.

**Evidence is named, not copied.** `assembler_version` and
`evidence_input_fingerprint` let a client correlate with the evidence route; the
bundle itself stays where it lives. `claim_conflict` is absent entirely — it is a
disagreement between records, not a reason a criterion went unmet, and putting it
beside a result is how a reader would come to treat it as one.

**In the PWA**, per criterion: what was expected, then `Met` / `Not met` /
`Could not verify`, then the reason as a short sentence. `unverified` renders in a
**different badge class and a neutral tone** from `not_met`, because one is a
finding about the work and the other is a statement about Cofferdam's reach.
A manual criterion shows its description, `Could not verify`, and no control to
mark it done. Fingerprints sit in a collapsed *Audit identifiers* section: they
are deterministic identities, not a trust score. There is no re-run control and no
check-runner control.

### Criterion continuity (M2K PR10, schema v9)

The fact PR9 identified as missing and refused to guess at. Every turn's
requirements were already stored; the **relationship** between two turns'
requirements was not, so neither *accumulate every turn* nor *only the latest
turn counts* could be correct and a task-level answer stayed unavailable.

Two additive tables, created empty and **never backfilled**:

| Table | What it holds |
| --- | --- |
| `task_turn_criteria_continuity` | one declaration per reserved turn: state, mode, both snapshot identities, a fingerprint, a relation count and a dispatch state |
| `task_turn_criterion_supersessions` | the lineage edges a partial revision names |

**It computes nothing.** No aggregate, no task verdict, no
`AGGREGATOR_VERSION`. A lineage edge is not a judgement.

**Three read states**, the same three-way shape criteria use:

| State | Meaning |
| --- | --- |
| `declared` | somebody stated a relationship; `mode` says which |
| `not_declared` | the turn was dispatched and **nobody stated one** — recorded on purpose |
| `legacy_unknown` | no row: the turn predates continuity persistence |

`not_declared` is the one that matters. Omitting the row would make "nobody
declared a relationship" and "we cannot know whether anybody did" the same
observation forever, and only the first is recoverable. It is **not** `extend`,
`replace`, `independent` or preserve-previous — it is the absence of an answer,
recorded, and it leaves a future aggregate honestly unavailable for that turn.

**Four modes**, and each answers the only question an aggregate has to ask —
*what happens to the requirements that were already live?*

| Mode | Predecessor | Relations | Meaning |
| --- | --- | --- | --- |
| `root` | none | none | no earlier snapshot exists for this task |
| `extend` | required | none | prior active requirements remain; this snapshot adds |
| `replace` | required | none | the prior active set is wholly superseded |
| `revise` | required | **at least one** | prior requirements remain **except** those explicitly superseded |

**`independent` is deliberately absent.** It answers neither "they remain" nor
"they are superseded", so an aggregate would have to pick one — the ambiguity
this vocabulary exists to remove. If prior requirements remain that is `extend`;
if they do not that is `replace`.

**`root` is derived but still checked.** It is a structural claim — *this task
has no earlier criteria snapshot* — not an inference about intent, which is what
makes deriving it safe. It is verified against the database and refused if an
earlier snapshot exists. A first turn whose criteria are `not_provided` is still
`root`: the mode describes lineage, and the criteria snapshot already records
that nothing was required.

**A `not_provided` follow-up proves nothing about continuity.** An empty criteria
set is not evidence that prior requirements were preserved, replaced or
unchanged, so such a turn is `not_declared` unless somebody declared otherwise.

**Supersession is a bounded many-to-many.** One old criterion may be superseded
by several new ones (a requirement split) and several old ones by a single new
one (a merge); neither needed a special case. Capped at **64 relations** and
**refused over the cap rather than trimmed**, because a silently dropped edge
would leave a requirement live that somebody had retired.

**Lineage is declared, never inferred.** Identical description, identical
criteria fingerprint, identical path and identical ordinal are all things two
unrelated criteria can share, and differing text does not disprove lineage
either. Both sides of an edge are durable criterion ids; the predecessor
criterion must belong to the **declared** predecessor snapshot. The caller names
the current side by **ordinal**, never by id, because current ids are minted
inside the same reservation moments earlier.

**The predecessor is a snapshot id**, not a turn number worked out at read time,
and it is validated to exist, to belong to **this task** — lineage never crosses
tasks — and to come from a **strictly earlier** turn.

**Frozen before the worker exists.** Reserved against the same turn number as the
criteria snapshot and the Git baseline, immediately after the snapshot it
describes, and frozen with both at `dispatch_started`. The adapter's first
instruction can observe all three as durable on a separate read-only connection
while `task_turns` still has no row. A retry of the same reserved turn, an
`AdapterRefusal` and a restart all leave the original declaration and its
fingerprint untouched; only a genuinely new turn may declare afresh.

**The foreign keys name `tasks` and `task_turn_criteria`, never `task_turns`** —
the same choice PR4's baseline and PR6's criteria made, because this is a
pre-work fact committed before the turn row exists.

**Authority is the user or a future host-owned planner.** Never the worker and
never the adapter: `AdapterOutcome` and `TaskContext` have no continuity field,
and worker prose, claims, Git evidence, the evaluator and the assessment route
can none of them create an edge. Like criteria since PR6, it is an **internal
`TaskService` argument** with no HTTP request field, no bridge Action and no PWA
control.

**`CONTINUITY_MODEL_VERSION = 1`**, distinct from the schema, criteria model,
assembler and evaluator versions, and bound into a deterministic
`continuity_fingerprint` over the state, mode, both snapshot identities and the
relations in canonical order. No clock, rowid, minted id, provider identifier or
host path reaches it.

### Effective post-worker path state (M2K PR14, schema v10)

The first of the three layers PR13 said were missing, and **only** the first. It
is evidence, not an answer: no predicate, no binding layer, no aggregate, no
`AGGREGATOR_VERSION`. `EVALUATOR_VERSION` stays 1, `ASSEMBLER_VERSION` stays 3,
and `EvidenceBundle` v3 does not carry final state.

Two additive tables, created empty and **never backfilled**:

| Table | What it holds |
| --- | --- |
| `task_turn_final_state` | one observation per turn: state, limitation reason, the lineage fingerprint that selected the targets, the HEAD anchor, a path count, an observation fingerprint |
| `task_turn_final_state_paths` | one row per target path: ordinal, path, path state, kind, reason |

**Final state means the working tree.** Not the committed HEAD tree: a worker
that deletes `foo.py` without committing has left a project in which `foo.py` is
gone, and a HEAD-only probe would call it present; one that creates `bar.py`
without committing has left it there, and a HEAD-only probe would call it absent.
The mistake runs in both directions, so nothing downstream repairs it. Not the
index either — `git rm --cached foo.py` empties the index and leaves the file on
disk, and *does this path exist in the effective workspace* is answered by the
filesystem. `head_revision` **is** stored, as an **audit anchor** saying which
committed revision the observation sat alongside; a worktree result that
disagrees with it is not a contradiction, because the two describe different
things.

**Three path states, and `absent` is a positive observation:**

| State | Meaning |
| --- | --- |
| `present` | an object exists at this path; `kind` says what sort |
| `absent` | the safe anchored lookup **completed** and determined nothing is there |
| `unavailable` | the lookup could not be completed safely; a closed reason says why |

An IO error, a permission refusal and a refused symlink traversal are therefore
`unavailable`, never `absent`. Collapsing *we could not look* into *it is not
there* is the single most damaging thing this surface could do, because a future
acceptance layer would read the second as evidence.

**Four kinds** for a present path — `file`, `directory`, `symlink`, `other` — and
**no content of any sort**: no bytes, digest, size, mtime, permissions, owner,
inode or directory listing. A path-state row carrying content would be a second
artifact surface arriving without its own review.

**Containment is the kernel's.** The verified root is opened, then every
intermediate component relative to the descriptor above it with `O_NOFOLLOW` —
the same discipline PR1's artifact observer uses — so an intermediate symlink is
refused by the kernel rather than by a comparison made afterwards, and
`repo/external -> /home/user/secrets` cannot be walked through to reach
`external/private.txt`. One detail is load-bearing: with `O_NOFOLLOW` an
intermediate **directory symlink** reports `ENOTDIR`, not `ELOOP`, which is
indistinguishable from *a regular file where a directory was expected* — so the
blocked component is `lstat`-ed to tell the two apart. Without that step an
escape attempt would have been recorded as `absent`, which is exactly the false
negative above. Traversal is refused **even when the link points inside the
project**, because a link that is safe today can be repointed tomorrow. A
*final*-component symlink is observed as itself without being followed, so a
broken symlink is `present`/`symlink` rather than absent.

**Targets are the PR11-resolved active criteria's paths** — `path` and `to_path`,
in the resolver's deterministic order, deduplicated by **exact equality only**.
Never the whole repository, which would be an unbounded read of somebody's
project at every turn boundary. Where lineage is unavailable (`not_declared`,
`legacy_unknown`, an unresolvable chain) there is no defensible target set and the
observation records `lineage_unavailable`; substituting the current snapshot would
be a guessed requirement set wearing an observation's clothes. A resolved
**empty** active set is a complete observation of nothing, and means nothing about
acceptance. A `manual` criterion contributes no path, which is not a gap.

**Three stored observation states, plus absence:**

| State | Meaning |
| --- | --- |
| `complete` | every target path was observed as present or absent |
| `incomplete` | at least one path could not be observed; the rest **are** stored, and no consumer may read it as whole |
| `unavailable` | no defensible path set existed; carries no paths at all |
| `legacy_unknown` | no row: the turn predates PR14, or the process died before the boundary |

The last two causes are deliberately **not** distinguished, because they mean the
same thing to every consumer: nothing was recorded, so nothing may be assumed.

**Taken at the boundary, never on read.** After the worker returns, after PR5's
committed-range observation, and **before the turn is durably closed**, all inside
the dispatch lock. A read returns the stored row and touches nothing — no
repository, no Git, no `stat`, no observer. If reading meant *go and look now*,
repository drift would silently rewrite historical answers, an audit could not be
reproduced, and a remote read would become a live probe of somebody's filesystem.
Deleting the project after capture changes no stored answer. **Write-once**: an
existing observation is never replaced, because it describes a moment that has
already happened.

**Boundary loss is never repaired.** No recovery path, and no migration, may
reconstruct a historical observation by looking at the filesystem now — that
would be a statement about today wearing yesterday's timestamp.

**The foreign key names `task_turns`**, unlike the pre-work facts of v6, v7 and
v9 which name `tasks`. Those must be durable *before* the turn row exists; this
one is taken after the worker returns, so a final state for a turn that never
happened is unrepresentable rather than merely unwritten.

**Bounded, and honest about stability.** At most **256 target paths** — chosen for
filesystem work at a turn boundary, deliberately not derived from PR6's
32-per-snapshot bound because an active set accumulates across turns — and
**refused over the bound rather than truncated**. The path set is read twice and
retried up to three times if it moves, then refused as `observation_unstable`
rather than reported optimistically. The limitation is stated rather than implied:
v1 observes existence and kind, so a file whose *contents* changed between passes
looks identical to both.

**An observation failure never rewrites the task.** A project that could not be
read is a gap in Cofferdam's evidence, not a fault in the user's work; a completed
worker stays completed and the gap is recorded as an explicitly unavailable
observation — the rule the Git baseline and committed range already follow.

**Nothing is reinterpreted.** `path_operation(foo.py, created)` asks what the
worker did; observing that `foo.py` is present does not satisfy it. No predicate
joins them, and a state predicate is its own PR.

**`FINAL_STATE_OBSERVER_VERSION = 1`**, distinct from the schema, criteria model,
continuity model, assembler, evaluator and resolver versions, and bound into a
domain-separated observation fingerprint over the observer version, the target,
the state and limitation, the lineage fingerprint, the HEAD anchor and every path
result in stored order. No clock, minted id, rowid or host path reaches it.

**Internal only.** `TaskService.turn_final_state(task_id, turn_number)` and the
store method beneath it. No HTTP route, no bridge Action, no Custom GPT Action, no
PWA control, and the PR8 assessment response is unchanged — a read surface is its
own review.

### Change claims and artifacts (M2K PR1, schema v4)

Two tables carry the **claim** side of evidence, which Task Core did not have. They are additive:
created by the same `CREATE TABLE IF NOT EXISTS` script every start already runs, with no existing
column moved, retyped or constrained, so a version-3 database becomes a version-4 one by being
opened and a task written before them simply has no claims.

| Table | What it holds | Provenance |
| --- | --- | --- |
| `task_change_claims` | what a worker **said** it changed: a closed operation (`created`, `modified`, `deleted`, `renamed`), a project-relative path, the turn it was claimed in | `adapter_reported`, always |
| `task_artifacts` | what Cofferdam **saw** when it opened that path: digest, byte size, bounded preview, or a reason it saw nothing | `os_observed`, always |
| `task_claim_ingestion` | how much of one report became stored claims: submitted, accepted and rejected counts, a truncation flag, and counts by closed reason code | host-owned bookkeeping |

**Why two tables and not more columns on one.** D-2026-08-11-6 says every field carries its source
kind. A single row holding both a claimed path and a Cofferdam-computed SHA-256 would need one
`source` value covering two different kinds of statement, and whichever it held would misdescribe
the other field. So `source` is not a stored column at all: it is a constant on each record type,
and no code path can write a claim that says it was observed.

**Why not `task_events.evidence_json`.** That column is a capped list of bounded *pointers* for
display, and it stays exactly what it was. A claim needs an identity an artifact row can reference,
a turn it belongs to, and a closed operation an evaluator can compare — none of which a JSON blob
on an event can carry without becoming a second schema nobody validates.

**Record time is when everything is decided.** The project root is resolved from the task's project
through the host-owned registry and re-verified at the moment of recording; the path is checked
lexically, then denied outright if it matches the code-owned secret-path list, then opened through
the descriptor-relative walk `mind/documents.py` owns — so a symlink at any component is refused by
the kernel rather than by a comparison afterwards. Denied content is never read, so it never enters
the database and cannot be served later by a surface that does not exist yet. The claim is still
recorded: that a worker claimed a credential file and Cofferdam refused to look is a fact worth
keeping.

**The deny policy recognises conventions; it does not scan content.** A path is denied when any
directory component is a credential directory (`.ssh`, `.gnupg`, `.aws`, `.docker`, `.kube`,
`.cofferdam`, `secrets`), or when its basename is a known credential name, a `.env` variant
(`.env`, `.env.local`, `.env-local`, `local.env`), or a credential extension (`.pem`, `.key`,
`.p12`, `.pfx`, `.jks`, `.keystore`, `.p8`, `.tfstate`, `.env`). One backup extension —
`.bak`, `.backup`, `.old`, `.orig`, `.save`, `~` — is stripped once and the name is asked again, so
`private.pem.bak` is denied while `notes.md.bak` is not.

It is deliberately not a detector. Files whose *names* contain a keyword — `docs/environment.md`,
`src/tokenizer.py`, `docs/secrets-design.md`, `config/database.example.yml` — are ordinary project
files and stay readable. The policy takes no argument and reads no configuration: an adapter cannot
widen it, and neither can a caller.

**A rename is checked on both sides.** `safe.txt -> .env` names a sensitive destination, and reading
the source because the source looked harmless would launder the destination's identity through it —
the file about to become `.env` is the file whose bytes those are. Either side denied means no
digest and no preview; the claim row survives with `path_denied_sensitive`, because a withheld
artifact is not a rejected claim.

**Claim ingestion is bounded, and the bound is visible.** Both limits — per outcome and per task —
and every deterministic validation refusal are **counted**, never silently applied. The counts land
in `task_claim_ingestion` in the same transaction as the claims they describe, so a crash cannot
leave a stored claim set with no record of how complete it is.

**Rejected submissions are represented without being stored.** There is no column for a refused
path, operation or label. A rejected path may be an absolute location, a traversal attempt or a
credential file name, and keeping one *for reporting* would be a second door into the database for
exactly the material the deny list exists to keep out. What survives is a count against a closed,
code-owned reason code.

Because of that, a future `EvidenceBundle` can distinguish two situations that otherwise look
identical once the process that did the counting has exited:

* a **complete** claim set — everything the worker reported was stored, and
* an **incomplete** one — some was refused or truncated, with the reasons counted.

**A refused claim and a refused observation are different facts, and stay different.** A claim whose
path is unusable is *rejected* and never becomes a row. A valid claim whose bytes could not be
read — a deleted file, a file that is gone, a path the deny list withholds — is *accepted*, becomes
a row, and carries its artifact reason. Reading the second as the first would report that a worker
sent nothing when it reported a deletion.

**None of this is an evaluation.** The ingestion summary reports completeness, not truth. It has no
field for verified, passed, matched, confidence or risk, and it says nothing about whether the
claims are accurate.

**No comparison lives here.** Nothing matches a claim against an observation, and no field records a
verdict, a confidence or a risk level. That is the next milestone's work, and doing it at record
time would let a claim become believed as a side effect of being written down.

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

GET  /api/tasks/{id}/turns/{n}/evidence                 one turn's derived evidence
```

The evidence route is **M2K PR2** and is described below. It is the one task
route the Actions bridge credential cannot reach.

The two clarification routes are **M2I PR2**. They carry information, never permission: the
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

## Turn evidence (M2K PR2)

`GET /api/tasks/{task_id}/turns/{turn_number}/evidence` returns one turn's
**derived** evidence bundle: what the worker claimed, what Cofferdam observed,
and the relationship between them.

**Turn-qualified, because turn ownership is the point.** Schema v5 records the
exact event-sequence range each turn owns — `task_turn_bounds`, written inside
the turn-open and turn-close helpers in the same transaction as the turn row.
Before v5 there was no such record, and it could not be reconstructed:
**timestamps are not an authoritative shared boundary** between a turn and an
event sequence, because two events can share a millisecond and `started_at` is
written by a different call than the one that allocates the sequence. A task-level
evidence route would have to merge turns or pick one, which is exactly the
confusion v5 exists to end.

**Derived, not stored.** There is no bundle table. Assembly reads claims,
ingestion summaries, append-only event evidence and the bounds — and runs no Git,
opens no file and calls no provider. A bundle therefore describes what was
*recorded*, not what the repository looks like now, and reading one changes
nothing: no event, turn, claim, artifact, ingestion row or bound is created, and
the task's `updated_at`, `lifecycle_revision` and `event_cursor` are untouched.

**What agreement means.** Since M2K PR3 the machine observation is
`git status --porcelain=v1 -z`, and the porcelain status **is** preserved: an
observation carries a closed machine change kind — `created`, `modified`,
`deleted`, `renamed` or `unknown` — and, for a rename, both paths. So the
vocabulary is:

* `path_agreed` — a claim and an observation name the same path. `path_agreement`
  is true. Since PR3 `operation_agreement` may be `true`, `false` or `unknown`;
  it is `unknown` for every observation recorded before PR3, and for unmerged
  paths, type changes and copies, which Git reports without establishing an
  operation. It is deliberately never a bare `agreed`.
* `claim_conflict` — the same path, and **explicitly incompatible** operations:
  the worker says created, the machine says deleted. `path_agreement` stays
  **true** (both records name the file); the disagreement is entirely about the
  operation. It is **not a verdict** — not a task failure, not an acceptance
  failure, not dishonesty. A worker that modified a file and then deleted it
  produced a conflict and did nothing wrong.
* `claim_only` — a stored claim with no eligible observation in this turn.
  Unmatched and unverified; **not** false, dishonest or contradicted. A worker
  that changed a file and committed it leaves a clean tree with nothing to match.
* `observed_only` — an observation no claim names. **Not** evidence of
  concealment: the claim set may be incomplete, which is why the completeness
  state is published in the same payload.

**Conflict requires a positive machine fact.** Absence is not conflict, and
neither is a pre-PR3 observation, an unmerged or type-changed path, or a
truncated observation set. Only two records that cannot both describe one path
produce one.

**Machine observation completeness is published.** `machine_observations_complete`
says whether Git reported more changes than Cofferdam recorded — through the
emitter's evidence budget, or because a path failed the safety gate. When it is
false, the absence of an observation at a path is **not** evidence that nothing
happened there.

**What `git status` cannot see.** It compares the index and working tree against
the **current HEAD**, so it is not a before/after comparison and **a worker that
commits its work leaves a clean tree and is not observed**. PR4 recorded the
missing boundary and PR5 reads the range from it, as its own
`committed_range` observation domain — see *Committed-work observations* above.
The two domains are never merged: a path may be committed inside a turn and
changed again afterwards, and both are true. A range whose boundary was already
dirty shows change and may not contradict a claim.

**Turns that predate v5** report `turn_attribution: legacy_unknown`, keep their
own claims, and receive **no machine observations at all** — nothing inferred
from timestamps, event types or the nearest sequence.

**Identity without copying.** `assembler_version` and a domain-tagged,
length-prefixed SHA-256 `input_fingerprint` identify the inputs a bundle was
built from, so a later record can refer to an evidence snapshot as
`(task_id, turn_number, assembler_version, input_fingerprint)`. Project-relative
semantic paths are inputs; absolute host paths, provider and session identifiers,
read time, live Git state and artifact previews are not. `generated_at` sits on
the response envelope as presentation metadata, never inside the bundle.

**Device token only.** This is the one task route the Actions bridge credential
cannot reach — it is guarded by `require_token`, which has never heard of that
credential. `GET` only, no root or path selector, no policy selector, no artifact
body. **No verdict of any kind**: no pass, fail, score, confidence or risk level,
and no field for one.

---

## Assessment aggregation doctrine (M2K PR9 — design only)

**Nothing in this section is implemented.** There is no aggregate in the database,
in any response, in the serializer or in the PWA, and no code computes one. This
is the contract a future aggregate must obey, written down **before** the named
check runner adds another mechanism that produces results — so those results
arrive to a consumer whose rules already exist rather than defining them by
accident. Decisions: D-2026-08-16-2 through D-2026-08-16-6.

### Three axes, kept apart

| Axis | Question it answers | Vocabulary |
| --- | --- | --- |
| Worker lifecycle | What happened to execution? | `completed`, `failed`, `interrupted`, `cancelled` |
| Acceptance | What do the recorded criteria and evidence establish? | `met`, `not_met`, `incomplete`, `not_assessable` |
| Verification reach | How far could Cofferdam see? | the evaluator's reason codes |

**`completed` does not imply `met`**, and the live database is the demonstration:
ten completed tasks, zero evaluations. A worker that ran to a clean stop has
described its own control flow and said nothing about whether it did what was
asked. **`failed` does not imply `not_met`** either — a turn can fail after
satisfying every criterion recorded for it.

This separation is the load-bearing rule. Everything M2K built — criteria frozen
before dispatch, evidence assembled from machine observation, evaluation frozen
after close — exists to stop a worker's account of its work from being the verdict
on it. An aggregate that read lifecycle as acceptance would undo all of it in one
line. Acceptance therefore never uses the words `success`, `failed` or `passed`:
those belong to lifecycle, and a reader who sees one cannot tell which domain it
came from.

### Per turn: two dimensions, not one enum

**Availability**, derived from the criteria state alone:

| Criteria state | Availability | Reason |
| --- | --- | --- |
| `present` | `assessable` | — |
| `not_provided` | `not_assessable` | `no_structured_criteria` |
| `legacy_unknown` | `not_assessable` | `historical_criteria_unknown` |

Neither absent case may ever be rendered `met`, `success`, `passed` or an empty
pass. `not_provided` means *Cofferdam knows none were supplied*; `legacy_unknown`
means *Cofferdam does not possess the question*. The reasons stay distinct —
collapsing them would merge "we never asked" with "we cannot know what we asked".

**Acceptance outcome**, which exists **only** when criteria are `present`:

| Outcome | Rule |
| --- | --- |
| `not_met` | at least one criterion result is a deterministic `not_met` |
| `incomplete` | no `not_met`, and at least one `unverified` |
| `met` | every criterion result is `met` |

Evaluated in that order. One known unmet requirement is already sufficient to know
the turn's recorded requirements were not all established, however many others are
unverified — and that is an **acceptance** result, never a lifecycle failure.
Otherwise any `unverified` yields `incomplete` and **never** `not_met`, preserving
the doctrine the evaluator was built on: an evidence limitation is not a finding
about the work. Known failure dominates, uncertainty blocks `met`, and nothing
else reaches `met`.

**What `met` means exactly:** *the acceptance criteria recorded for this turn are
all established as met by the current assessment model.* It does **not** mean the
task succeeded, the worker succeeded, the user's full intent was captured, or that
a later turn cannot regress it.

### Manual criteria cap the outcome

A `manual` criterion always evaluates to `unverified`, because no human-answer
channel exists. **Any `present` snapshot containing one therefore cannot currently
reach `met`** — it is capped at `incomplete`. That is the honest state and it is
recorded rather than worked around. Manual completion is never inferred from
worker prose, a PWA interaction, a claim, or a model judgement. A human-answer
channel is new authority and new state, and needs its own reviewed design.

### Blockers are context, not a competing outcome

`requires_human` must not become a fourth aggregate value. When a turn has both an
unanswerable manual criterion and an incomplete machine observation, a single
value has to suppress one of them:

```
evidence criterion -> unverified because the Git boundary was incomplete
manual criterion   -> unverified because human judgement is required
```

The recommended shape is orthogonal context beside the outcome — `requires_human`,
`machine_verification_incomplete` — so the turn says `incomplete` **and** says
exactly why, and a caller composes both instead of guessing which was hidden.

`claim_conflict` is **excluded from aggregation entirely**. It is a disagreement
between an adapter's record and the machine's: not a criterion result, not a task
failure, not a blocker. It stays on the evidence surface as audit context.

### Task level: unavailable, and that is the decision

Per-turn acceptance is well defined. **Task-level acceptance across turns is
unavailable** until criterion continuity semantics exist. Both obvious rules are
demonstrably wrong:

*Accumulate all turns.* Turn 1 requires `foo.py` created; turn 2 requires it
removed. Held simultaneously active, the task's own requirements contradict each
other and no outcome is correct — when in fact the second turn was a legitimate
change of mind.

*Latest turn only.* Turn 1 requires feature X and tests; turn 2 adds logging.
Honouring only turn 2 silently drops X and the tests, and the task can report
`met` while its original requirements were never established.

The missing fact is the same in both: every turn may carry its own immutable
criteria snapshot, and Cofferdam persists **nothing** about whether a later
snapshot replaces, extends, narrows, supersedes one specific criterion, reverses
one, or is an independent concern. Reporting "per-turn acceptance is available;
task-level acceptance is not yet defined" is a true sentence a caller can act on.
A confidently wrong task verdict is not.

### What continuity will require

Design constraints, written before any of it existed. **M2K PR10 built the
persistence and M2K PR11 built the resolver** — see *Active criteria at a turn*
below for what shipped; the constraints are kept as written because they are what
the implementation was held to:

* **Explicit, never defaulted** — `replace` and `extend` are indistinguishable by
  inspecting the criteria, and a wrong default applies silently to every task.
* **Snapshot-level relation plus criterion-level relations** — a later turn
  routinely supersedes one requirement while its siblings stay live, which
  `supersedes_snapshot_id` alone cannot express.
* **Fingerprints are not lineage** — identical text does not prove it, differing
  text does not disprove it. An aid, never the authority.
* **The planner or the user is the authority; the worker is not and the adapter
  never is.** A worker that could declare its new criteria supersede its old ones
  could retire the requirement it had just failed.
* **Frozen pre-dispatch**, alongside the criteria snapshot — a boundary a worker
  can move after seeing its results is not a boundary.
* **An additive schema version**, and a hard prerequisite for any runtime
  task-level aggregate.

### Versioning and derivation

A future aggregate carries its own code-owned `AGGREGATOR_VERSION`, distinct from
the schema version, `ASSEMBLER_VERSION`, the criteria model version and
`EVALUATOR_VERSION`. Composition doctrine changes for its own reasons, and a
change of doctrine must not silently reinterpret answers recorded under the old
one.

It should be **derived on read, not persisted**. A per-turn aggregate is a pure
deterministic function of an immutable `EvaluationRecord` and its criteria
snapshot, so persisting it stores nothing that cannot be recomputed while adding a
write path to a surface whose central property is that reading it mutates nothing.
Deriving it makes a doctrine change a re-render rather than a migration. If
historical audit later needs *what did we say at the time*, that argues for
recording the aggregator version beside the answer — its own decision, when a real
need appears.

## Active criteria at a turn (M2K PR11)

PR6 stores what each turn required. PR10 stores what each turn said about the turn
before it. PR11 composes the two into the question anything downstream asks first:
**given those immutable declarations, which immutable criteria are in force at
this turn?**

It computes no aggregate. There is no `AGGREGATOR_VERSION`, no task verdict, and
no code here that could produce one. A resolved active set says *what is currently
required*; whether any of it happened is the per-criterion evaluator's question,
and what a mixture of those results means remains unavailable by design.

`TaskService.resolve_active_criteria(task_id, turn_number)` is the entire surface.
**Internal only** — no HTTP route, no Actions Bridge operation, no PWA control,
and the assessment response is unchanged.

### The four modes

| mode | active set |
|---|---|
| `root` | this snapshot's criteria; no predecessor is traversed |
| `extend` | the predecessor's resolved active set, then this snapshot's criteria |
| `replace` | this snapshot's criteria; the predecessor's active set is **not required** |
| `revise` | the predecessor's resolved set minus every explicitly superseded criterion, in place, then this snapshot's criteria |

Nothing is deduplicated by description, path or fingerprint. Criterion ids are the
only identity — two turns may legitimately require the same-looking thing for
different reasons, which is the whole reason lineage is declared rather than
inferred.

### `replace` is a cut point

An unknown predecessor makes `extend` and `revise` **unavailable**: both are
statements *about* the prior active set, so they cannot be answered without it. It
does **not** block `replace`, which says the prior set is gone whatever it was.

That is what lets a task recover. A turn that predates continuity
(`legacy_unknown`), or one nobody declared anything for (`not_declared`), no
longer poisons every later turn forever — the moment somebody declares `replace`,
the requirement set is knowable again.

The predecessor's *identity* is still validated for `replace`: it must exist,
belong to this task, and come from an earlier turn. Only the traversal is skipped.
A malformed `root` is never reinterpreted as `replace`.

### Ordering

Inheritance first, and stable. Surviving inherited entries keep their relative
order; superseded entries are removed **in place** with nothing promoted into the
hole; this turn's own criteria follow in stored `ordinal` order. Never sorted by
criterion id, description, path or fingerprint — the order somebody submitted
requirements in is a fact about the requirements.

### Unavailable is an answer, and so is empty

Unavailable results carry a closed code-owned reason and **no partial active
set** — a caller given half an answer would use it. The vocabulary:
`continuity_legacy_unknown`, `continuity_not_declared`, `predecessor_unavailable`
(carrying the underlying cause and the turn it broke at), `predecessor_missing`,
`predecessor_foreign_task`, `predecessor_not_earlier`, `criteria_snapshot_missing`,
`continuity_snapshot_mismatch`, `root_has_predecessor`, `root_not_first_snapshot`,
`relations_mode_mismatch`, `supersession_target_not_active`,
`supersession_predecessor_unknown`, `supersession_current_unknown`,
`duplicate_active_criterion`, `malformed_lineage`, `lineage_depth_exceeded`,
`cycle_detected`.

A **resolved** result with zero active criteria is different, and means *the
declared requirement set is empty*. It does not mean the task passed, acceptance
was met, or anything succeeded.

### A supersession must name an *active* criterion (M2K PR12)

A relation is valid only if its old-side criterion is active in the resolved
predecessor set. Historical membership is not active membership: a criterion
retired two turns ago cannot be retired again.

**This is now the rule at both ends.** PR10 originally validated the old side
against the criteria *stored in* the declared predecessor's snapshot, which is a
narrower thing and refused a legitimate revision of an inherited requirement — a
criterion introduced at turn 1 and still live at turn 2 through an `extend` is
part of what turn 2 stands on, but it is not one of turn 2's rows. The only
workaround was to declare turn 1 as the predecessor, which silently cut turn 2's
own criteria out of the lineage. PR12 replaced the check with the active-set rule
the resolver already used.

Allowed old sides are therefore: the predecessor snapshot's own criteria, anything
it inherited through `extend`, and anything that survived an earlier `revise`.
Still refused: a criterion an earlier `revise` retired, one a `replace` cut away,
one from another task, a nonexistent id, and a criterion of the *current* snapshot.
Refusals are **atomic** — one bad relation and the whole declaration writes nothing.

**A `revise` whose predecessor lineage cannot be resolved is refused before
dispatch** with `continuity_predecessor_lineage_unavailable`: `not_declared`,
`legacy_unknown` or malformed all mean there is no set for the revision to be a
revision *of*. It is never downgraded to `replace`.

**Write-time prevention does not retire the read-time check.** A restored
database, an imported fixture or a future bug can still present a stale relation,
so `supersession_target_not_active` remains, alongside snapshot mismatch,
cross-task and later-turn predecessors, impossible roots, mode/relation
disagreement, duplicate active ids, cycles and over-deep chains. Nothing is
repaired on read.

**One algorithm, one transaction.** Validation calls the same pure resolver over
the same lineage fetch the read path uses, inside the write transaction that
persists the declaration — so validation and persistence see one database state,
and a second copy of the fold cannot drift from the first.

**No version moved.** Schema stays v9, `CONTINUITY_MODEL_VERSION` stays 1 and
`RESOLVER_VERSION` stays 1. A stored relation always meant *this new criterion
retires that old one* and never carried a claim about which snapshot the old one
sat in, so nothing is reinterpreted, rewritten or revalidated —
`continuity_fingerprint` is byte-identical for the same declaration. What changed
is which declarations are accepted.

### Derived, versioned, bounded, pure

**Derived on read, never persisted.** No schema change; v9 stays. The sources are
immutable and the function is deterministic, so persisting the answer would add a
write path, a recovery path and a second place for the truth to live.

**`RESOLVER_VERSION = 1`**, distinct from `SCHEMA_VERSION`,
`CRITERIA_MODEL_VERSION`, `CONTINUITY_MODEL_VERSION`, `ASSEMBLER_VERSION` and
`EVALUATOR_VERSION`, and bound into a domain-separated resolved fingerprint over
the target, the ordered active set and every consumed lineage step. A `replace`'s
untraversed predecessor is deliberately **not** bound.

**Bounded** at `MAX_LINEAGE_DEPTH = 256` with a visited set, so a corrupted row
answers rather than hangs. **One deferred read transaction** across the whole
fetch, so a lineage can never be half-old and half-new. **Pure resolver**: no
SQLite, filesystem, Git, subprocess, socket, provider, environment or clock, and no
mutation.

D-2026-08-16-10 through D-2026-08-16-14.

## Cross-turn acceptance (M2K PR13 — doctrine only)

PR11 answers *which criteria are active at turn N*. This section answers the
question that has to be settled before anything aggregates them: **what is the
current acceptance state of an active criterion that was evaluated at an earlier
turn?**

Nothing here is built. No schema, no route, no runtime, no `AGGREGATOR_VERSION`.

### Every v1 predicate is a change observation

Read from the evaluator rather than from the vocabulary: `_evaluate_path_changed`,
`_evaluate_path_operation` and `_evaluate_rename` each consult only
`_attributable_observations(bundle, path)` — observations attributed to *the turn
being evaluated*. None of them reads repository state.

| predicate | asserts | class | re-evaluable later? |
|---|---|---|---|
| `path_changed(P)` | the worker produced a resulting change at P **this turn** | action/change | mechanically yes, semantically no |
| `path_operation(P, OP)` | the worker performed OP at P **this turn** | action/change | mechanically yes, semantically no |
| `rename(S, D)` | the worker renamed S→D **this turn** | action/change | mechanically yes, semantically no |
| `manual` | a person must check | undecidable | no |

So the vocabulary says *"the worker did X"*, never *"the project now satisfies X"*.

### Why the three obvious answers are all wrong

**Carry the old result forward.** A stored `met` does not survive a later
regression; a stored `not_met` does not survive a later repair.

**Re-evaluate against the target turn.** A **category error**, not an
approximation. `path_operation(foo.py, created)` asked at turn 2 means "did turn
2's worker create foo.py". A well-behaved turn 2 that correctly leaves the file
alone answers *no*, and the evaluator renders that as `not_met` once closure is
complete. This manufactures a false negative most reliably when the work was
correct.

**Inherited met stays met unless superseded.** Continuity is **user intent about
requirements**, authored and frozen before the turn ran. An intent cannot certify
a state of the world that came after it.

### No result value is monotonic

`met` can be regressed. `not_met` can be repaired — and a stale `not_met` telling
somebody their completed fix failed is not the "safe" error, it is just the other
wrong one. `unverified` usually records a limitation of the *observer* (inexact
attribution, a dirty pre-work boundary, unread coverage), every one of which can
be gone next turn.

Therefore **no stored per-turn result may be reused as a current answer without
independent current evidence.** Stored records are not invalidated by this; they
remain true of their own turns.

### What Cofferdam can and cannot prove

It can sometimes prove a later change **happened** at a path. It can never prove
one **did not**, for three independent reasons:

1. **Observation only happens inside turn windows.** Between one turn's post-work
   observation and the next turn's pre-work baseline — and after the last turn,
   up to the moment of reading — the repository is unobserved.
2. **Both domains are diffs, not state.** `worktree` records paths differing from
   HEAD; `committed_range` records paths changed in the range. Neither enumerates
   what exists. *Narrowed by PR14*: a turn observed under schema v10 now has a
   stored answer to "did `foo.py` exist **at that turn's boundary**" for the paths
   its active criteria named. That does not lift reason 1 — the gaps between
   boundaries remain unobserved — and it is evidence, not a predicate.
3. **Absence already cannot be read inside one window** when attribution is
   inexact, the pre-work boundary was dirty, or coverage was incomplete.

**One free check exists today.** A turn's `CommittedRangeSummary.target_revision`
and the next turn's `GitBaseline.head_revision` are both stored, so committed
drift *between* turns is detectable by comparing two stored strings, and the next
turn's `working_tree_state` says whether the tree matched HEAD at that instant.
That proves a gap is non-empty, never what happened in it — enough to answer
`unavailable` honestly, not enough to certify anything.

### What is required, in dependency order

1. A **final-state evidence surface** — does this path exist. A genuinely new
   evidence primitive, since every existing one is a diff. **Delivered by PR14**,
   with one refinement to the phrasing above: *at this revision* was the wrong
   frame. The authority is the **working tree** at the post-worker boundary, not a
   committed revision, because a worker that deletes a file without committing has
   changed what the project is. See *Effective post-worker path state* under
   **Persistence**.
2. **Final-state predicates** (`path_exists` / `path_absent` conceptually),
   re-evaluable at any turn because they describe the project rather than a
   worker's behaviour. **Not built.** PR14 stores the observation and stops there;
   adding a predicate moves `EVALUATOR_VERSION` and is its own review.
3. Only then a **cross-turn binding layer**.

Exact-turn evidence is **not** weakened to get there. `EvidenceBundle` v3 keeps
its meaning; anything cross-turn is a new derived layer *over* per-turn bundles.

### The missing layer

Per criterion active at the target turn: criterion identity; target turn; origin
turn and snapshot; a current result of `met`/`not_met`/`unverified`; the identity
of the evidence or evaluation supporting **that** result; and a provenance marker
— *newly evaluated*, *carried under an explicit rule*, *invalidated by later
evidence*, or *unavailable*. The provenance field is the point: an answer that
cannot say why it believes itself is not auditable.

It gets **its own semantic version** when it is built — not `RESOLVER_VERSION`,
not `EVALUATOR_VERSION`, not a future `AGGREGATOR_VERSION`. Binding a result to a
turn across a lineage is a fourth distinct operation.

### Per mode

**`root`** — every active criterion originates here, so PR7's evaluation already
is a current binding.

**`replace`** — cuts evaluation history exactly where it cuts lineage. Criteria
before the cut are not active after it, so no cross-turn binding is ever needed
for them and no evidence traversal crosses that point. The resolver's walk already
stops there, so the two layers agree by construction.

**`revise`** — survivors need the cross-turn rule; newly added criteria bind
directly to the target turn; superseded criteria leave the active set and their
stored evaluations must never contribute to a current answer.

**`extend`** — every inherited criterion needs the cross-turn rule, and today none
of them can get one.

**`manual`** — unchanged. Inherited or not, `unverified` at every turn it is
active. Continuity never promotes it, and only a human-answer channel could.

**`not_declared` / `legacy_unknown`** — the resolver already answers *unavailable*,
so acceptance is unavailable too. No composition through unknown continuity.

### What blocks the aggregate

PR9's ordered rule — any `not_met` dominates, any `unverified` yields
`incomplete`, only all-met yields `met` — remains valid doctrine. Its **inputs**
are what do not exist. The aggregate is unblocked when either every active
criterion originates at the target turn, or final-state predicates make inherited
criteria answerable.

A narrowing deliberately **not** taken: a `root`/`replace`-only aggregate would be
sound today, and is a trap — its correctness would depend on a lineage shape the
caller does not control, and the first `extend` would break it silently.

### Where named checks fit

A host-owned named check ("the tests pass") is inherently a **current-run**
property: answered by running it now, so final-state by nature and re-evaluable at
any turn. It solves the cross-turn problem for criteria that use it, and solves
nothing for path criteria — one of the two roads out, not a detour. Its trust
boundary is unchanged and still binding (D-2026-08-11-7).

### Safety default

Where the current status of an active criterion cannot be established, the answer
is **unavailable/unverified**. Never a reused stale `met`, never a reused stale
`not_met`, never inferred preservation.

D-2026-08-16-15 through D-2026-08-16-19.

### Why the named check runner comes after this

The runner introduces the first project-scoped command execution authority, a new
recorded result type, probably a new criterion kind and a `check_id`, invocation
and result persistence, timeout and output policy, and an evaluator semantic
expansion that would move `EVALUATOR_VERSION`. Built first, every one of those
results would feed a consumer with no defined contract.

Its trust boundary is unchanged and still binding (D-2026-08-11-7): host-owned
definitions by stable id, literal `argv`, `shell=False`, validated project `cwd`,
bounded timeout, bounded output, off by default per project, and **neither the
caller nor the adapter ever supplies command text**.

---

## Current-state assessment: identity and storage (M2K PR15 — doctrine only)

PR14 delivered the evidence. This settles **where a result derived from it may
live and what it must prove**, before any state predicate exists — because the
first `path_exists` that is written will need somewhere honest to put its answer,
and the wrong home is very hard to leave later.

### What a PR7 evaluation already means

Read off the merged code, not inferred from names:

> the judgement of **turn N's own criteria snapshot**, against **turn N's own
> `EvidenceBundle`**, under `EVALUATOR_VERSION` 1.

`_evaluate_one_turn` reads `turn_criteria(task_id, turn_number)` and
`evidence_bundle(task_id, turn_number)` — the same turn twice.
`record_evaluation` derives every identity from one `CriteriaSnapshot` and
refuses unless the result count equals that snapshot's criterion count.
`evaluation_fingerprint` binds one turn, one snapshot, one bundle. **Origin turn
and target turn are the same number**, and nothing stored distinguishes them,
because until PR11 they could not differ.

That meaning is **frozen**. It must never be widened to "all criteria active at
turn N": every stored row was written under the narrow one, and no reader could
tell which meaning a given row carries, because nothing recorded would say.

### The exact schema, and what it does *not* constrain

| Table | Identity |
| --- | --- |
| `task_turn_evaluations` | PK `evaluation_id`; FK `(task_id, turn_number)` → `task_turns`; FK `criteria_snapshot_id` → `task_turn_criteria`; carries `evaluator_version`, `criteria_state`, `criteria_fingerprint`, `assembler_version`, `evidence_input_fingerprint`, `result_count`, `evaluation_fingerprint`; **`UNIQUE (task_id, turn_number, evaluator_version)`** |
| `task_turn_criterion_results` | PK `(evaluation_id, criterion_id)`; FK `evaluation_id`; FK `criterion_id` → `task_turn_criterion_items`; `UNIQUE (evaluation_id, ordinal)`; `result IN ('met','not_met','unverified')` |

Two consequences matter more than the columns.

**The results table does not tie a criterion to its evaluation's snapshot or
turn.** Its foreign key says the criterion exists *somewhere*. Probed against a
real v10 database, the DDL **permits** turn 1's criterion inside turn 2's
evaluation, and permits an evaluation whose `turn_number` and
`criteria_snapshot_id` belong to different turns. Only `record_evaluation`'s
snapshot-driven API refuses them. So the honest meaning cannot be defended by
adding rows here — the database would not stop a dishonest one.

**One target turn admits exactly one evaluation per evaluator version.** Two
evaluation semantics cannot share a turn without overloading `EVALUATOR_VERSION`
to mean "a different kind of question", which is not what it means.

### Change criteria and state criteria are different questions

| | asks | answered from |
| --- | --- | --- |
| `path_operation(P, created)` | did **this turn** create P? | turn-local `EvidenceBundle` |
| `path_exists(P)` | at this turn's **final-state boundary**, is P there? | PR14 `FinalStateObservation` |

A change criterion is answerable only at its own turn. A state criterion is
answerable at **any** turn that has an observation — which is exactly what makes
an inherited requirement assessable, and exactly why the two must not share one
provenance shape.

### Same turn is not the easy case it looks like

Even when a `path_exists` criterion originates in the turn being assessed, the
existing record cannot hold its answer honestly: the parent row's only evidence
identity is `assembler_version` + `evidence_input_fingerprint`, which names the
`EvidenceBundle`. A result derived from a final-state observation would sit under
a provenance pointing at an input it did not use. The minimum fix is
final-state provenance — which is the same change the inherited case needs, so
there is no cheaper same-turn shortcut.

### The inherited case, and origin vs target

A criterion introduced at turn 1 and still active at turn 4 has **two** identities
— where it came from, and where it is being assessed. A single `turn_number`
column can only tell the truth about one. `source_turn_number` and
`source_snapshot_id` already exist on PR11's `ActiveCriterion`; they are simply
never persisted. The assessment layer keeps them **beside** the target turn, never
collapsed into it.

**Every target-turn assessment is retained.** `met` at 1, `met` at 2, `not_met` at
3, `met` at 4 is four immutable rows — four machine judgements at four world
boundaries. There is no mutable "current status" row, because overwriting would
erase the only record that something broke and was fixed. *Current* means **at the
target turn**, not *latest*.

### What a state result must prove

**Authority** — the result may not exist without all of it: the target turn; the
criterion id; the **final-state observation fingerprint**; `FINAL_STATE_OBSERVER_VERSION`
(what `present` *means* is that version's semantics); the **resolved active-lineage
fingerprint**, proving the criterion was active at the target turn rather than
merely present in history; and the layer's own semantic version.

**Redundant audit context** — kept for legibility, never relied upon: the origin
turn and snapshot (derivable from the criterion row), the observation id
(derivable from the target turn, one observation per turn), and the HEAD anchor
(already inside the observation).

`EvidenceBundle` v3 is **not** the vehicle. Stuffing a final-state observation into
it would reinterpret `ASSEMBLER_VERSION` and change what every stored
`evidence_input_fingerprint` refers to, merging the two evidence meanings PR13 and
PR14 separated.

### Reading an observation safely

PR14 records a state per **path**, so completeness is a per-path question:

| Observation / path | State result |
| --- | --- |
| path row `present` | evaluable (`met` for `path_exists`, `not_met` for `path_absent`) |
| path row `absent` | evaluable (the mirror) |
| path row `unavailable`, or no row for that path | `unverified` |
| observation `unavailable` or `legacy_unknown` | `unverified` |

An `incomplete` observation does **not** poison the paths that were observed: a
path row reading `present` is individually complete. A missing observation is never
`not_met`.

### Intended predicate semantics, when they are built

`path_exists(P)` — `met` when P is `present`, `not_met` when `absent`,
`unverified` otherwise. `path_absent(P)` — the mirror. `path_exists` means **any
filesystem object** exists at P: PR14 already records kind separately, so folding
kind into the predicate would ask two questions with one word. **No kind
predicates**, and none are proposed here.

### Lineage consequences

**`replace`** cuts the active set, so criteria before the cut need no current
assessment at or after the replace turn — the resolver already stops there.
**`revise`** removes superseded criteria from the active set (no further
assessment) and keeps survivors eligible; newly added criteria begin at the
current turn. **`not_declared` / `legacy_unknown`** resolve to unavailable, so
current acceptance is **unavailable** — never a latest-snapshot fallback, never an
accumulation of all historical criteria, never text matching.

### The hard boundary: no semantic conversion

State predicates are **authored**, never derived. `path_operation(P, created)`
does not become `path_exists(P)`; `path_operation(P, deleted)` does not become
`path_absent(P)`; and **continuity may not perform this transformation in any
mode**. A requirement to create a file is silent about whether it must still be
there nine turns later, and inventing that requirement enforces something nobody
asked for.

PR14's observation scope does not imply state-evaluability either: it observes
paths named by whatever criteria are active, which today are all change criteria.
Observing a path is not permission to answer a change criterion from it.

### Named checks stay possible

A future named check — a bounded host-owned execution, "the tests pass now" — is a
**third** input domain: neither turn-change evidence nor a path observation. The
assessment layer therefore carries an explicit **evidence-domain discriminator**
from the start, so that domain joins as a value rather than as a fourth table.
Nothing here is implemented.

### Versions

`EVALUATOR_VERSION` keeps its turn-change meaning and does not move.
`FINAL_STATE_OBSERVER_VERSION` keeps its observation meaning. The mapping from
*active criterion + evidence domain* to *current result at a target turn* is a
**fourth distinct operation** and takes its own version. A future
`AGGREGATOR_VERSION` folding legitimate current results is a fifth. None is
overloaded to save a constant.

### Persisted or derived

Every input is immutable and versioned — the criterion row, the stored
observation, and a resolver that is itself pure — so the assessment is a pure
function and **any version's answer can be recomputed on demand**. Persistence
therefore buys a cache and a drift tripwire (the role `EvaluationConflict` plays
for PR7), not correctness. The recommendation is to **derive first**, with the
identity and fingerprint defined now so persistence can be added later without
changing what the answer *is*.

### What is still blocked

**Before `path_exists` / `path_absent`:** the criterion vocabulary must admit
them, and that is **not additive** — `task_turn_criterion_items` pins `predicate`
in a `CHECK`, SQLite cannot alter a `CHECK`, and the table is referenced by a
foreign key from `task_turn_criterion_results`. It needs a full table rebuild of
immutable historical criteria: the first destructive-shape migration this project
would have performed.

**Before the aggregate:** one legitimate *current* result for every active
criterion at the target turn. That needs the layer described here, and manual
criteria remain `unverified` at every turn regardless.

---

## Current criterion assessment (M2K PR16, derived, no schema)

PR11 says which criteria are in force at turn N. PR7 says what the worker did
during turn N. This answers the question an aggregate would have to ask and
neither of them does:

> for every criterion **active** at turn N, what current result can Cofferdam
> legitimately establish **at N**?

Derived from immutable rows, versioned, and stored nowhere.

### One rule: evidence must match the criterion's semantics

Every machine predicate in this build — `path_changed`, `path_operation`,
`rename` — is a *turn-change* observation. That makes it answerable at its own
turn and nowhere else, which produces exactly three cases:

| Active criterion | Current result at turn N |
| --- | --- |
| change predicate, origin turn **= N** | PR7's stored judgement for turn N, read and never recomputed |
| change predicate, origin turn **< N** | `unverified` — `inherited_change_not_current_state_evaluable` |
| `manual`, any origin | `unverified` — `manual_criterion_no_machine_authority` |
| unknown predicate | `unverified` — `unsupported_predicate` (total, like PR7's evaluator) |

### Why an inherited change criterion is `unverified`

Three answers are tempting and PR13 showed all three are wrong.

**Carrying the old result forward** reuses a statement about turn 1 as a
statement about turn 4. It misses later breakage when it was `met` and later
repair when it was `not_met`, and nothing in the record says which happened.

**Re-evaluating against turn N's evidence** asks *did this turn create `foo.py`?*
of a requirement satisfied three turns ago and correctly left alone since. The
honest answer is *no* — so this would report `not_met` precisely when the work
was right.

**Reading PR14's final state** — `foo.py` is present, so `created` is met — is
the semantic conversion the milestone forbids. PR14's observations exist on
`main`; this layer does not import `finalstate` at all.

`unverified` is therefore the accurate answer, not a placeholder: Cofferdam has
no evidence of the right kind at this boundary. Pinned in all three directions —
an origin `met`, `not_met` and `unverified` produce **identical assessment
fingerprints**, so the origin cannot be recovered from the current answer.

### Origin turn and target turn are never collapsed

Every assessment carries `source_snapshot_id`, `source_turn_number` **and**
`target_turn_number`, and both turn numbers are fingerprinted. The same criterion
assessed at a different turn is a different fact.

### Only the active set

Assessed criteria are exactly PR11's resolved active set, in its order,
unre-sorted. Superseded criteria and criteria cut by a `replace` are **absent**
from the answer rather than present-and-unverified — they are not required here.
A resolved **empty** set is a legitimate answer meaning the declared requirement
set is empty; it does not mean acceptance was met.

### Four refusals, deliberately not one

| Refusal | Means | Changes by waiting? |
| --- | --- | --- |
| `turn_not_closed` | not a completed boundary | yes |
| `lineage_unavailable` | PR11 cannot determine the active set | no |
| `evaluation_not_recorded` | **operational** — the turn is closed, PR7 has not run yet | yes |
| `evaluation_inconsistent`, `unsupported_evaluator_version` | a stored row violates service-owned invariants, or unknown evaluator semantics | no |

`evaluation_not_recorded` is set-level on purpose: reporting a pending recovery
pass as a set of `unverified` criteria would file a gap in Cofferdam's own
pipeline as a statement about the user's work. It is **never** `not_met`.

A turn whose active set is entirely inherited or manual needs no PR7 record and
does not wait for one — nothing there could be answered by an evaluation.

### Stored evaluations are validated, not trusted

PR15 proved the DDL permits dishonest combinations. The binder checks the record
belongs to this task and turn, names the target turn's own snapshot, has a
declared count matching the results it carries, answers no criterion twice, and
answers **every** same-turn criterion. It fails closed and **repairs nothing** —
a read that fixed a row would destroy the evidence that something wrote it.

Supported evaluator versions are **enumerated**, not assumed: a future
`EVALUATOR_VERSION` 2 may decide criteria differently, and binding its results as
though they meant version 1's thing is the silent reinterpretation this layer
exists to prevent.

### Versioning and identity

**`CURRENT_ASSESSMENT_VERSION = 1`**, distinct from `SCHEMA_VERSION`,
`CRITERIA_MODEL_VERSION`, `CONTINUITY_MODEL_VERSION`, `ASSEMBLER_VERSION`,
`EVALUATOR_VERSION`, `RESOLVER_VERSION`, `FINAL_STATE_OBSERVER_VERSION` and the
future aggregate version. It owns the mapping *active criterion + evidence domain
→ current result at a target turn* and nothing about how any underlying judgement
was reached.

The per-criterion fingerprint binds the assessment version, criterion identity
**and origin**, target turn, kind and predicate, evidence domain, result, reason,
and the exact PR7 `evaluation_fingerprint` where one applies. The envelope binds
the version, target, set state and reason, PR11's **lineage fingerprint**, and
every criterion fingerprint in order. Not bound: any clock, the minted
`evaluation_id`, rowids, host paths, provider or session identifiers.

An **evidence-domain** discriminator (`turn_change` / `not_applicable`) is carried
from the start, so a future `final_state` or `named_check` answer cannot hash
equal to a V1 one.

### Derived, and read from one snapshot

No table, no schema change — v10 stands — no write path and no recovery path.
Lineage, the turn's lifecycle and the evaluation are read inside **one deferred
read transaction**. That matters more than it did for PR11: criteria and
continuity are frozen at dispatch, but an evaluation row is written *later* by
bounded recovery, so an active set read before that commit combined with an
evaluation read after it would describe a database state that never existed.

### Internal, and still not an aggregate

`TaskService.current_criterion_assessment(task_id, turn_number)` and nothing
else. No HTTP route, no bridge Action, no Custom GPT Action, no PWA control, and
PR8's assessment response is unchanged. There is no verdict, no outcome, no count
of what was met, and no `AGGREGATOR_VERSION`. This produces the legitimate
per-criterion inputs an aggregate would need — and the aggregate stays blocked
until state predicates exist, because today an inherited requirement can only be
`unverified`.

---

## The criteria vocabulary and the v11 rebuild (M2K PR17)

Two predicates were added — `path_exists(P)` and `path_absent(P)` — and **nothing
evaluates them**. This section is about what that costs and why it is safe.

### Five predicates, in two families

| Family | Predicates | Asks |
| --- | --- | --- |
| change | `path_changed`, `path_operation`, `rename` | what did the worker **do** during this turn? |
| state | `path_exists`, `path_absent` | what **is** true at this turn's final-state boundary? |

`path_exists` means **any** filesystem object — file, directory, symlink or
other. PR14 already records kind separately, so folding kind into the predicate
would ask two questions with one word; there are deliberately no kind
predicates. `path_absent` is not "something deleted it": a path that never
existed satisfies it too.

Validation needed no new rules. A state predicate requires a `path`, refuses an
`operation` and refuses a `to_path`, all from the existing structure — and it
reuses the **same** path gate as every other criterion, so `../escape`, absolute
paths, `~`, embedded NULs and the sensitive deny list are refused exactly as
before. A criterion grants no filesystem access; observation remains PR14's job.

### Why this required a table rebuild

`task_turn_criterion_items` enumerates the predicate list in a `CHECK`, and
SQLite has no `ALTER TABLE ... DROP CONSTRAINT` — both verified by attempting
them rather than assumed. So the table is rebuilt. **The intentional delta is
exactly one clause**: the other eleven checks already constrain the new
predicates correctly, because a state predicate is not `path_operation` (so
`operation` must be NULL), is not `rename` (so `to_path` must be NULL), and is an
evidence kind (so `path` is required).

This is the **first destructive-shape migration in this project**. Every earlier
step was a pure `CREATE TABLE IF NOT EXISTS`.

### Foreign keys, which are the actual risk

Three keys point at the table: `task_turn_criterion_results` (`CASCADE`) and both
sides of `task_turn_criterion_supersessions` (`CASCADE` and `RESTRICT`).
Measured, not reasoned about:

* with enforcement **on**, `DROP TABLE` is **refused** by the `RESTRICT` side;
* with enforcement **off**, the child rows survive untouched — no cascade fires.

So enforcement is suspended for the rebuild, **outside** the transaction, because
`PRAGMA foreign_keys` is a no-op inside one — confirmed empirically, not trusted.
It is restored in a `finally` and the restoration is **verified**: a connection
that silently continued without enforcement would be worse than a failed
migration. `PRAGMA foreign_key_check` runs inside the transaction before the
commit, so a rebuild that orphaned anything rolls back.

The new table is built aside and renamed **in**, never the reverse: modern SQLite
rewrites `REFERENCES` clauses in *other* tables on rename, so renaming the old
table out of the way would repoint all three foreign keys at the doomed table.
The one artifact is cosmetic — `RENAME TO` stores the name quoted, so a migrated
database reads `CREATE TABLE "task_turn_criterion_items"` where a fresh one reads
it unquoted. A test asserts the quoting is the **only** difference.

### Interruption

The migration is interrupted at every step — before the new table, during the
copy, before the drop, during the rename, at the foreign-key check, at the
commit, at the transaction start — and each time the database is still v10 and
whole, with every row, no half-built table, clean integrity and foreign keys, and
a retry that then succeeds.

Completion is detected from the **stored DDL, not the version number**, and the
version bump is deliberately last. A crash between the rename and the bump
therefore leaves a database whose shape is already correct, and the next open
does nothing rather than rebuilding an already-rebuilt table.

**No backfill and no conversion.** Historical predicate strings are exactly what
they were; `path_operation(P, created)` was not rewritten to `path_exists(P)` and
never will be.

### Representable before evaluatable

This was the question that decided whether the PR could exist: can the vocabulary
land before anything understands it? Yes — and by prior design rather than luck.

| Layer | Answer for a state predicate |
| --- | --- |
| PR7 evaluator | `unverified` / `unsupported_capability` — it dispatches on a predicate table and this is the seat reserved for a capability that does not exist yet |
| PR16 binder | `unverified` / `unsupported_predicate` — the branch was written and tested with `path_exists` literally, before the predicate existed |
| PR11 resolver, PR10 continuity | predicate-agnostic; a state criterion is inherited, superseded and cut exactly like any other |
| PR14 observer | contributes the criterion's `path` to the bounded target scope, and never sees the predicate |

Verified end to end rather than argued: the turn evaluates, the evaluation record
is complete and valid with the right result count, the turn closes normally, the
current assessment resolves — and **no `met` or `not_met` appears anywhere**.

PR14 recording `present` for the path is target *selection*, not interpretation.
Observing that a path exists is not deciding a criterion, and nothing joins them.

### Versions

Only the schema moved: **v11**. `EVALUATOR_VERSION` stays 1,
`CURRENT_ASSESSMENT_VERSION` stays 1, `FINAL_STATE_OBSERVER_VERSION` stays 1,
`CRITERIA_MODEL_VERSION` stays 1 — the existing criteria fingerprint already binds
predicate and path honestly, so `path_exists(foo.py)` and `path_absent(foo.py)`
hash differently with no new fingerprint version, and neither hashes like the
`path_operation` criterion it superficially resembles.

### Rollback stops being clean once v11 is written to

A slot flip cannot walk a schema backwards, so a rollback is a **pair**: the old
runtime plus a verified pre-v11 backup.

* **Before** any v11-only criterion exists — restoring that backup is a clean
  point-in-time downgrade.
* **After** a `path_exists` or `path_absent` criterion has been stored — restoring
  it **destroys requirements a user actually stated**, because the old schema
  cannot represent them.

Those two cases must never both be called "simple rollback".

---

## Limitations of this milestone

Stated in the API payload as well as here, because a client should never have to
infer them:

* Cofferdam reports what an adapter tells it; adapter-reported evidence is not
  observation. Since M2K PR2 the two can be shown side by side per turn — but
  **path agreement is not operation agreement, and neither is a verdict**.
* An interrupted task is never resumed automatically — restarting the service
  ends it.
* Task Core runs no shell, no process and no model. What a task does is
  entirely the adapter's.
* Follow-up and cancellation are offered only where the adapter declares support.
* Secrets are never task content and have no field here.
* Since M2K PR6 a turn can carry acceptance criteria, since M2K PR7 each one gets
  a deterministic `met`/`not_met`/`unverified` answer, and since M2K PR8 both are
  readable through one private turn-qualified route and a PWA panel. **Nothing
  aggregates those answers.** There is no task verdict, no pass/fail, no
  confidence, no risk and no check runner in this build, and a criterion result is
  never a statement about whether the task succeeded.

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
