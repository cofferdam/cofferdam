# The Claude Agent SDK adapter — M2I PR1–PR4

A second Lane B transport to the same agent, on the same provider-neutral
[Agent Task Core](AGENT_TASK_CORE.md). Where the
[Claude Code adapter](CLAUDE_CODE_ADAPTER.md) parses a CLI's stream-json on
stdout, this one drives the official **Claude Agent SDK** and receives typed
messages — which is what makes a structured question channel possible at all.

This document describes what was built in **M2I PR1** (the foundation), **M2I
PR2** (the structured clarification round trip), **M2I PR3** (same-session
follow-up and the `get_result` boundary) and **M2I PR4** (the phone surface,
helper cleanup and startup reconciliation), and is deliberately clear about what
was not.

> **This adapter is off by default and is not deployed.** Everything below is
> evidenced by the published SDK distribution and by automated tests that call
> nothing — except the clarification schema and round trip, which were settled by
> two **supervised live spikes** in a disposable project against a
> non-production daemon, recorded in
> [The question schema](#the-question-schema-verified-by-the-m2i-pr2-live-spike)
> and [What the M2I PR3 live spike found](#what-the-m2i-pr3-live-spike-found).
> Where a claim would need evidence nobody has gathered, it is marked
> **outstanding** and is not made.

## What is *not* here

Stated up front, because a reader's first question about a second agent
transport is what it cannot do and what it does not yet replace.

- **It does not replace the Claude Code adapter.** Both may be registered, they
  have different ids, and the CLI adapter remains the fallback and the only one
  validated live from a phone against this host. `ROADMAP.md` holds the
  retirement rule: the CLI adapter goes only after verified parity.
- **No production change.** No systemd unit, drop-in, installer or registry file
  in this repository enables it, and none was edited by PR1, PR2 or PR3.
- **No production validation.** The live spike ran against a *non-production*
  daemon with a temporary `COFFERDAM_HOME` and a temporary registry copy. The
  live service, its drop-in and the live registry were never touched, and nothing
  here claims the adapter has been validated in production.
- **Three schema variants remain unobserved** — multiple questions in one input,
  a genuinely multiple-choice question, and a free-text question. The reader
  handles all three conservatively; nobody has seen the provider produce them.
- **No PWA question UI.** PR2 validates the round trip through the authenticated
  API. Rendering it on a phone is separate work.
- **No follow-up across a restart.** Same-session follow-up arrived in PR3 and
  needs the helper process to still be alive. If the daemon restarted, the
  conversation is over and a follow-up is refused as
  `task_session_unavailable`. Cross-process `resume` is **not used** and is not
  evidenced — see [Restart, honestly](#restart-honestly).
- **No tool approval from a phone, ever.** Not "not yet": a permission request is
  denied by a code-owned handler and recorded, and there is no route, no table
  and no field through which one could be granted. See
  [Clarification is not approval](#clarification-is-not-approval).
- **No *public* `get_result`.** PR3 adds `GET /api/tasks/{id}/result` as a
  private authenticated route on the workstation daemon. It is not an Action,
  it is not proxied, and the Custom GPT bridge that will one day call something
  like it does not exist.
- **No Custom GPT bridge.** `future_gpt_bridge` is a reserved word in the
  provenance vocabulary — shared by clarification answers and follow-ups — and
  is **not** in the set of sources any route accepts. A vocabulary entry is not
  an enabled surface.
- **No shell, no transcript reading, no prompt injection, no auto-resume.** Same
  as Lane B has always been.

## The SDK, verified

Read from the published `claude-agent-sdk` source archive and wheel rather than
recalled. `cofferdam/workstation/tasks/adapters/claude_agent_sdk/sdk.py` carries
the same record next to the code that depends on it.

| | |
|---|---|
| Distribution | `claude-agent-sdk` |
| Import package | `claude_agent_sdk` |
| Version verified against | `0.2.134` |
| Requires-Python | `>=3.10` (Cofferdam supports 3.9) |
| License | MIT |
| Runtime dependencies | `anyio>=4`, `sniffio>=1`, `mcp>=1.23,<2`, `typing_extensions` below 3.11 |
| Session API | `ClaudeSDKClient` — `connect`, `query`, `receive_messages`, `interrupt`, `disconnect` |
| Configuration | the `ClaudeAgentOptions` dataclass |
| Messages | `AssistantMessage`, `UserMessage`, `SystemMessage`, `ResultMessage`, `TaskStarted/Progress/Notification/UpdatedMessage`, `StreamEvent`, `RateLimitEvent` |
| Content blocks | `TextBlock`, `ThinkingBlock`, `ToolUseBlock`, `ToolResultBlock`, `ServerToolUse/ResultBlock` |
| Permission channel | `can_use_tool` returning `PermissionResultAllow` / `PermissionResultDeny` |
| Errors | `ClaudeSDKError`, `CLIConnectionError`, `CLINotFoundError`, `ProcessError`, `CLIJSONDecodeError` |

Three findings that changed the design rather than merely being noted:

**The wheel is about 91 MB and bundles its own CLI** at
`claude_agent_sdk/_bundled/claude` (CLI `2.1.226` in this release). Cofferdam
does not use it. The adapter pins `cli_path` to the CLI already installed on the
host — the one whose sign-in the workstation manages, the one the Claude Code
adapter drives, and the one re-verified immediately before every launch.

**`can_use_tool` requires a streaming prompt.** Passing a string to `connect()`
with a permission callback installed raises. So the adapter calls `connect()`
with no argument and delivers the prompt with `query()`, which is the shape that
gets both a bounded permission policy and a working `interrupt()`.

**`ClaudeAgentOptions.env` is an override map, not a replacement.** The SDK's
transport builds the child environment as
`{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **options.env, …}`. See
[The environment difference](#the-environment-difference), which is the one place
this adapter is weaker than the CLI one.

**One thing could not be verified.** The SDK package contains no
`AskUserQuestion` type and no schema for one — checked, not assumed: the string
does not occur anywhere in the published archive. That tool belongs to the CLI.

Two further facts PR2 read out of the same source, both of which decided a
design:

**Control requests do not block the read loop.** `Query._spawn_control_request_handler`
dispatches with `spawn_detached`, so a `can_use_tool` callback that takes its
time keeps messages arriving and `interrupt()` landing. That is what makes it
possible to hold a question open while somebody answers.

**A custom `Transport` silently drops the permission wiring.** When
`can_use_tool` is set, `ClaudeSDKClient` puts `permission_prompt_tool_name="stdio"`
on a *copy* of the options and hands that copy to its own transport; the client's
source says plainly that "the materialized options never reach a pre-constructed
transport". A custom transport must therefore reproduce that undocumented detail
in its own argv or the callback never fires. See
[Why a helper process](#why-a-helper-process-and-not-a-custom-transport).

## The optional dependency boundary

The adapter is an extra. "Optional" here means something stronger than a
different key in `pyproject.toml`:

```bash
pip install -e ".[agent-sdk]"
```

- exactly one function imports the SDK — `sdk.load()` — and it is called from
  inside adapter methods, never at module scope;
- importing `cofferdam.workstation` and **building the registry with the adapter
  enabled** do not import it, asserted in a fresh interpreter;
- describing the adapter *does*, and that is the feature rather than a leak: the
  only honest way to answer "can this be loaded" is to try. The same test pins
  the boundary at exactly that point, so construction quietly starting to import
  would fail it;
- a missing SDK is a precise sentence naming the install command; an old
  interpreter gets a *different* sentence, because on 3.9 no install would help;
- an installed-but-incompatible SDK names the attribute it is missing;
- the entire event model, tool policy and adapter behaviour are tested on a
  machine without the SDK, so the stdlib-only CI job keeps its meaning.

The extra carries a `python_version >= '3.10'` marker and a `<0.3` upper bound.
The bound is deliberate: the SDK is pre-1.0 and self-declared alpha, so a minor
bump is allowed to change the API this adapter was written against.

## Enablement

Off by default, three host-owned ways to turn it on, none reachable from a
request:

```bash
python -m cofferdam.workstation --enable-claude-agent-sdk-adapter
```

or `"enable_claude_agent_sdk_adapter": true` in
`$COFFERDAM_HOME/config.json`, or `COFFERDAM_ENABLE_CLAUDE_AGENT_SDK_ADAPTER=1`
in the unit's environment.

The flag is **one-directional and independent**: it never clears
`--enable-claude-code-adapter`, and enabling it does not select it for anything.
A project still has to list `claude-agent-sdk` in its `adapters` array before a
task may use it. The daemon announces the adapter on every start, and says
explicitly that the Claude Code adapter is unaffected.

Two adapters answering to one id is now a start-up failure rather than a silent
overwrite — `DuplicateAdapterId`, raised when the registry is constructed.

## The normalized event model

`cofferdam/workstation/tasks/delegated.py` is **provider-neutral** and lives in
Task Core, not in the adapter. Twelve kinds:

`session_started` · `activity` · `output` · `clarification_requested` ·
`tool_approval_requested` · `tool_started` · `tool_finished` · `succeeded` ·
`provider_failed` · `cancellation_requested` · `cancelled` · `interrupted`

Each event carries the provider id, the provider session id, the provider's own
sequence number, an optional provider event id, a timestamp, and bounded text.
Every string has passed a sanitizer that removes ANSI escapes, C0/C1 control
characters and bidirectional overrides, normalizes to NFC, and truncates to a
bound named in that module. Per-kind bounds differ on purpose: an activity line
is a badge, a final result is something a person asked for.

**No provider payload can be stored.** There is no `raw`, `data`, `payload` or
`message` field on any class in the file, so an SDK object cannot be carried even
by mistake — a property asserted by test rather than by review.

`DelegatedEventLog` owns the three things an event stream gets wrong:

- **duplicates** — an event whose provider event id has been seen is dropped;
- **order** — arrival order is preserved and provider order is recoverable;
  out-of-order arrivals are counted rather than silently corrected;
- **finality** — once a terminal kind is accepted, nothing else is. This is the
  rule that stops a provider result landing a moment after a cancellation from
  resurrecting the task.

Events **project onto Task Core's existing generic event storage**. No schema
migration, no second table, no second database of delegated tasks. The store
already gives transactional append, monotonic per-task sequencing and duplicate
suppression; a second event schema would be a second history for one task.

## Clarification is not approval

The safety boundary this milestone exists to establish, and the one PR2 had to
carry all the way to an answerable surface without letting it blur.

|  | Clarification | Tool approval |
|---|---|---|
| What is asked | information or a choice | permission to act |
| Carries | a question, bounded options | a tool name, a coarse category |
| Cannot carry | any tool field | any question or option field |
| Waiting reason | `clarification` | `approval` |
| Adapter capability | `clarifications=True` | `approvals=False` |
| Durable row | `task_clarifications` | **none, and none planned** |
| API route | list + answer, authenticated | **none, and none planned** |
| May be answered remotely | yes, from the PWA today | **never** |

They are two dataclasses with disjoint required fields and disjoint serialized
shapes. Each `from_dict` refuses three ways: a wrong `category`, the *presence of
any of the other's fields* even when the category looks right, and a payload with
nothing usable in it. An event cannot hold both, and a request cannot be attached
to an unrelated kind. All six refusals are tested in both directions, and the
same refusal is tested again *over the helper pipe*, so the separation holds on
the wire and not only in memory.

The SDK's permission callback is a code-owned handler that **denies and
records**. It is handed the tool's input — the command, the path, the arguments —
and reads only the name; that material is exactly what makes approvals worth
keeping on a trusted surface, and copying it into an event a phone renders would
defeat the arrangement. `tool_input` appears in exactly one file, as a parameter,
is never subscripted and never has an attribute read off it, and is passed only
to the two conservative readers in `question.py` — asserted from the syntax tree.

**Only one of the two is a wait.** The adapter has one code path that can produce
`waiting_for_user(clarification)` and **no path at all** that can produce
`waiting_for_user(approval)`. An approval is not a wait: Cofferdam denied it, the
agent carries on, and reporting "NEEDS YOU" about a request nobody can act on
would be the same false claim the Claude Code adapter had to unlearn when a
finished turn was reported as waiting for an answer.

The answer endpoint refuses an approval-shaped body by name — `approval_id`,
`tool_name`, `tool_input`, `behavior`, `decision`, `allow`, `deny`,
`permission_mode`, `command`, `path`, `cwd`, `argv`, `env`. The last five are not
fields of any Cofferdam type at all; they are the shapes somebody would reach for
if they were trying to make this endpoint approve something, and refusing them by
name turns "that is not what this route is for" from a comment into a test.

## The tool profile

The **action** tools are identical to the Claude Code adapter's, and the sameness
is the point: two transports for one policy about what the agent may *do*, and a
difference there would mean switching transport quietly changed what it could do
to the workstation. A test asserts they match, and a second asserts the only gap
between the two profiles is the question tool — which does nothing to a machine.

| Option | Value | Why |
|---|---|---|
| `tools` | `Read, Write, Edit, Glob, Grep, AskUserQuestion` | **No Bash.** A shell inside an approved root is still a general shell on the workstation, reachable by writing English into a phone. `AskUserQuestion` acts on nobody and is the reason M2I exists. |
| `disallowed_tools` | `Bash, BashOutput, KillShell, Task, WebFetch, WebSearch, NotebookEdit` | Redundant by construction, and worth it: this is the list a reader checks first. |
| `permission_mode` | `acceptEdits` | File edits inside the root, without an interactive prompt nobody could answer headless. Not shell access. |
| `allowed_tools` | `[]` | Pre-approving a tool is the decision this milestone keeps on a human surface. |
| `mcp_servers` / `strict_mcp_config` | `{}` / `True` | No MCP server configured anywhere on this machine is loaded. |
| `setting_sources` / `settings` | `[]` / `None` | SDK isolation mode. The profile in source is the profile that runs. |
| `add_dirs` / `extra_args` | `[]` / `{}` | One directory; no argument that could turn the profile back into a suggestion. |
| `hooks` / `agents` / `plugins` / `skills` | none | Nothing programmatic adds capability. |
| `system_prompt` / `model` / `effort` | `None` | No invisible behavioural difference between the two transports; no model id from an API caller. |
| `max_turns` / `max_budget_usd` | `24` / `2.00` | A runaway agent stops on its own. |
| `cwd` | the server-resolved project root | Never a request value. |
| `cli_path` | the host's installed CLI | Not the SDK's bundled copy. |

**`bypassPermissions` is forbidden permanently.** It is listed by name in
`FORBIDDEN_PERMISSION_MODES` alongside `dontAsk` and `auto`, refused by
`verify_option_values` at build time — not only under test — and asserted by a
source scan that allows the string to appear in `options.py` and nowhere else.

`build_option_values` takes four parameters: a project root the server resolved,
a session id this process minted, a CLI path from the fixed host search, and a
mapping used for testing. There is no parameter for an executable, a tool list, a
permission mode, a model, an environment, a flag or a working directory, and the
signature itself is asserted.

### The environment boundary (M2I PR2)

The one place PR1 was weaker than the CLI adapter, and the first thing PR2 fixed.

**The finding.** `subprocess_cli.py` builds its child's environment as
`{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **options.env, …}`. So
`ClaudeAgentOptions.env` is an **override map layered over whatever the calling
process has**, never a replacement, and there is no option, keyword or seam in
the supported API that hands the SDK a complete environment. A daemon that ran
the SDK in-process would give an agent session its own token, its API keys, its
Cloudflare and Tailscale credentials and everything else in the unit file.

**The fix.** Cofferdam owns the *spawn* instead of the *transport*. The adapter
starts a helper process with its own `Popen` and a complete code-owned
environment, and the SDK runs inside it. Because that process's `os.environ`
**is** the allowlist, the SDK's merge produces the allowlist.

| | |
|---|---|
| Executable | `sys.executable` — the interpreter already running |
| Argument vector | `["-m", "cofferdam...claude_agent_sdk.host"]`, three constants |
| Shell | none; `shell=False` |
| Environment | `hostenv.build_child_environment()`, by **selection** |
| Working directory | the server-resolved project root |
| stderr | discarded, so nothing can corrupt a frame or reach a log |
| Process group | its own, so a Ctrl-C at the daemon cannot reach a task |

The allowlist is thirteen names and is **identical to the Claude Code adapter's**
— `HOME`, `PATH`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `LC_CTYPE`,
`TERM`, `TMPDIR`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` — plus four
forced values (`PYTHONIOENCODING`, `NO_COLOR`, `CLAUDE_AGENT_SDK_CLIENT_APP`,
`CLAUDE_CODE_ENTRYPOINT`). `HOME` is the load-bearing one: it is how the host CLI
finds its own sign-in. Cofferdam grants *reachability*, never *possession* — it
never opens a credential file, a keychain or a token store.

`PYTHONPATH` is added only so the helper imports the same Cofferdam that launched
it, derived from this package's own `__file__`, and the helper **removes it from
its own environment** before constructing anything from the SDK. So the CLI
grandchild sees exactly the session environment.

Both halves are enforced and both are tested. The parent passes `env=`; the child
refuses to run if what arrived is not what that produces
(`host._verify_own_environment`). A helper started by hand, by a supervisor, or
by a future caller that forgot the argument stops rather than handing a shell's
environment to an agent.

**What PR1's mitigation became.** `ENVIRONMENT_BLANKED` is now empty, and the
emptiness is the change: blanking `COFFERDAM_TOKEN` was a denylist protecting the
one name somebody thought of, and an allowlist protects every name nobody thought
of. `LD_PRELOAD` and a variable somebody adds to the unit file next year are both
absent — not because they are on a list, but because they are not on *the* list.

Errors never carry values. `EnvironmentPolicyError` names the offending **key**
and nothing else, and `environment_key_names` is the only function that describes
an environment; there is deliberately no counterpart returning its contents.

### Why a helper process, and not a custom `Transport`

`Transport` *is* a public export and `ClaudeSDKClient` *does* accept one, so this
choice needs justifying rather than asserting. Three reasons, all from the
published source:

1. **The vendor documents the ABC as removable.** Its own docstring says it "may
   change or remove" in any future release and that custom implementations must
   be updated to match. That is a poor foundation for a security boundary.
2. **A custom transport silently breaks the permission callback.** The
   `permission_prompt_tool_name="stdio"` copy never reaches it, so `can_use_tool`
   stops firing unless Cofferdam reproduces an undocumented internal detail — and
   that callback is where a tool is denied and a question is intercepted. The
   failure mode is the safety boundary going quiet without an error.
3. **It would move the whole profile into copied code.** With a custom transport
   the SDK applies none of `tools`, `permission_mode`, `setting_sources` or
   `strict_mcp_config`; every one would have to be re-derived from a ~230-line
   internal command builder this project does not control.

The helper costs one process and a bounded newline-delimited JSON protocol, and
buys a guarantee that rests on a mechanism Cofferdam controls and tests. It also
puts a 91 MB pre-1.0 async dependency out of the daemon's address space, so a
crash there cannot take the workstation service down.

The protocol is four commands down (`start`, `answer`, `cancel`, `close`) and six
messages up. **No command carries a path, an executable, an environment, a tool
name, a permission decision or a CLI flag** — the helper's entire configuration is
decided at launch. Normalization runs *inside* the helper, so what crosses the
pipe is already the bounded provider-neutral event shape, rebuilt on the parent
side through the same constructors that validate an in-process event.

## The structured clarification round trip (M2I PR2)

### The question schema, verified by the M2I PR2 live spike

`AskUserQuestion` is in the tool profile now — it is the one tool the SDK
transport has that the CLI transport cannot support, because a CLI stream offers
no channel an answer could travel back on. It acts on nobody: it reads no file,
writes none, and runs no command, which is why adding it does not weaken the
"two transports, one policy" rule. A test asserts the gap between the two
profiles is exactly this one tool.

Its input schema **was not in the SDK distribution** — the string does not occur
anywhere in the published archive — so PR2 shipped a deliberately conservative
reader and then settled the question with a supervised live spike. Two runs in
the disposable `claude-sandbox` project, against a non-production daemon, with
`tools=["AskUserQuestion"]` and no filesystem tool in the session at all.

The observed input, captured sanitized — key names and **type names**, never a
value:

```json
{"questions": [{"header": "str", "multiSelect": "bool",
                "options": "list", "question": "str"}]}
```

Options carried `label` and `description`. **No `value` key was present**, which
is why `read_question` falls back to the label — a guess before the spike, an
observation after it.

**The reader was not broadened.** The observed shape matched what this file
already read, field for field. `header` is a real field the build deliberately
ignores, and a test now says so, so a future change that starts reading it has to
change that test.

`question.SCHEMA_VERIFIED` is therefore `True`, `OBSERVED_SCHEMA` records the
shape, and every stored clarification carries the value that was true when it was
stored — a later build cannot retroactively claim yesterday's questions were
verified, and PR2's own first spike run left rows saying `false`.

**What the spike did not establish** is kept in `SCHEMA_EVIDENCE_OUTSTANDING`,
because a verified schema is not a verified variant:

- more than one question in a single tool input;
- `multiSelect: true` — a genuinely multiple-choice question;
- a question offering no options, answered as free text.

The reader already handles all three conservatively. What is missing is evidence
that the provider produces them, and a later spike that sees one should move it
out of that tuple rather than widen any parsing.

The reader is still allowed to fail. A shape it cannot defend produces **no
clarification at all** — a bounded observation goes into the history instead and
the agent is told nobody was asked. A missed question costs a task that keeps
running with an activity line; an invented one shows somebody a question the
agent never asked and then sends their answer to a model as though it had. Those
two mistakes are not the same size.

### What the spike found wrong

One real defect, which is what a spike is for: the durable clarification row was
being written with a **null `provider_session_id`**. The adapter had the id and
never passed it, so the one piece of evidence that an answer resumed *the same*
conversation was not being kept. `AdapterOutcome` now carries
`clarification_session_id` and `clarification_sequence`, the core stores both,
and a test asserts them.

One cosmetic artifact, recorded rather than hidden: because Cofferdam answers by
**declining** the question tool, the turn's tool result comes back as an error and
the history shows a bounded `A tool reported an error.` line immediately after
`An answer was delivered to Claude.` That is truthful — the tool genuinely did
not run — and suppressing it would mean tracking `tool_use_id` through the
normalizer to know which result belonged to the question.

### Where a clarification comes from

**Only from the permission callback**, never from the message stream. The rule:

> A clarification event is created only where an answer can actually be
> delivered.

Nothing a reader of `receive_messages` can do will get an answer back to a
blocked turn. PR1 produced one from the `ToolUseBlock` because there was no
answer channel at all and recording the question somewhere beat losing it; now
that there is one, doing both would give a task two pending questions for one
thing the agent asked, only one of which could ever be answered. The message-block
path records bounded activity and does not read the tool input.

### How an answer is delivered

As the `message` of a `PermissionResultDeny`. That is the conservative choice and
it was made from what the source proves:

- it uses only typed, documented API — the SDK turns it into
  `{"behavior": "deny", "message": …}`, verified in `query.py`;
- **it grants nothing.** The question tool never executes, so no unverified
  interactive path is relied on inside a headless session;
- the session is unchanged — same helper, same client, same provider session id —
  so continuation is a property of not having torn anything down.

What is *not* claimed: that allowing the tool with an updated input would also
work. It might; it is unverified, it is unused, and there is no code that does it.

### Two channels, not one

`submit_answer` and `send_followup` are separate methods on the session boundary
and reach the provider through entirely different channels. An answer resolves a
question the agent is blocked on; a follow-up is a new instruction to a session
waiting for nothing. A single method would have to decide which — from state, at
the worst possible moment. Follow-up is still refused truthfully.

### The durable model

`cofferdam/workstation/tasks/clarifications.py`, provider-neutral, in Task Core.
One new table in the **same** SQLite database — `task_clarifications`, schema
version 2, additive only — rather than a second store.

| Field | Note |
|---|---|
| `question_id` | Cofferdam-minted, random, opaque. Never derived from the question text or from a provider id. |
| `task_id`, `provider` | |
| `provider_session_id`, `provider_event_id`, `provider_sequence` | Kept for provenance; **not published to a client**. |
| `question` | Bounded, sanitized, ≤1000 chars. |
| `answer_mode` | `single_choice` · `multiple_choice` · `free_text` · `unknown` |
| `options` | ≤8, each with a **Cofferdam-generated** `option_id` (`opt1`, `opt2`, …), a bounded label and an optional bounded description. |
| `schema_verified` | What was known when this was stored. |
| `requested_at`, `status`, `answered_at`, `answer` | |

`status` is `pending` · `answered` · `cancelled` · `superseded`. The last two stay
distinct on purpose: one means a person stopped the task, the other means the
provider moved on, and somebody reading a history deserves to know which.

Duplicate suppression is a **unique index** on `(task_id, provider_event_id)`,
enforced by the database rather than by a check somebody has to remember.

Malformed or oversized data fails truthfully. Too many questions, too many
options, an option list that is present and unreadable, a question with no
question in it — each is refused, and the task is unaffected because a refused
question was never applied.

### Answer provenance

Every accepted answer records `actor`, a **source from a closed code-owned
vocabulary**, `received_at`, the accepted/rejected outcome, the bounded answer,
the resulting transition, and the provider/session identity of the question.

Sources are `workstation_pwa`, `internal_test` and the reserved
`future_gpt_bridge`. The last is in the vocabulary and **not** in
`ACCEPTED_ANSWER_SOURCES`, so a route that tried to use it is refused.

`source` is assigned by the route from the authenticated request context and is
not a body field — a client choosing how its own answer is attributed is the
opposite of what provenance is for. There is no display name, no header, no
address, no user agent and no token, and no field one could be put in.

The task history records the *shape* of an answer — "Answer received (1 option(s)
chosen)" — and never its text, exactly as it already does for a follow-up.

### What the provider actually receives

`clarifications.encode_answer` is the only function that turns an accepted answer
into text a model will read, and it is deliberately dull: Cofferdam's own
connecting words, the labels of the options **Cofferdam itself stored** — reached
through the identifiers the client sent, never through a string the client sent —
and the person's own text, unaltered.

There is no template read from a payload, no format string, no instruction
sentence and nothing a client can put into the *structure* of the message rather
than its content. This matters more here than anywhere else in the codebase: it is
the one place where text that arrived over the network becomes text a language
model acts on.

### The lifecycle

```
running → waiting_for_user (clarification) → running → …terminal
```

Both edges already existed in Task Core's graph; PR2 added no state and no
transition. Task Core remains the lifecycle authority — the adapter *reports* a
question and the core decides whether the task may enter that state, mints the
question id, and writes the row.

**The question and the state change are one transaction.** `TaskStore.transition`
takes `open_clarification` and `close_clarifications`, because a task saying
`waiting_for_user` with no question, or a pending question on a task that says
`cancelled`, is a disagreement between two rows a person would have to resolve by
guessing.

Held properties, each with a test:

- one active clarification per provider turn;
- a duplicate provider question event opens no second question;
- a stale, superseded or already-answered question cannot be answered;
- an answer cannot target another task — the lookup is scoped in the query, so a
  question id from elsewhere simply does not match;
- cancelling a waiting task closes its question as `cancelled`;
- an answer after cancellation is refused and never reaches the provider;
- a late provider result cannot resurrect a cancelled task;
- an answer the provider did not take is a **refusal**, and the question stays
  open — recording it would show somebody their answer accepted while the agent
  sat waiting for it.

### Restart, honestly

A restart while a task is waiting produces `interrupted`, and the question is
closed as `superseded` **in the same write**.

That is not a limitation being worked around. The session that asked was a
process, that process is gone, and nothing anybody typed now could reach it.
Leaving the question `pending` would put a task in the "needs you" bucket with an
answer box whose only possible outcome is a refusal — which is exactly the false
claim `interrupted` exists to avoid.

No adapter claims `recover_after_restart`. Cross-process session resume by id
(`options.resume`) exists in the SDK and is **not used**: it is a separate,
evidence-backed path and this build has no evidence for it.

### The authenticated routes

```
GET  /api/tasks/{task_id}/clarifications
POST /api/tasks/{task_id}/clarifications/{question_id}/answer
```

Both require the device token. The answer body accepts exactly two fields —
`answer` and `option_ids` — and an unexpected key is **refused, not ignored**.
There is no field for a session id, a project, a path, a tool, a command, a
permission mode or an allow/deny decision, and a body carrying one is refused
twice: once by the route's allowlist and again by name inside
`ClarificationAnswer.from_request`.

The list response carries no provider session id, no provider event id, no tool
input and no filesystem path.

Status codes: `404` unknown question (the same answer whether it never existed or
belongs to another task), `409` already closed, `422` an answer that does not fit
or an adapter that does not ask questions, `502` accepted-but-not-delivered.

**There is no approval route.** Not a disabled one, not a stubbed one that always
refuses — none. And no generic "answer a request" endpoint shared by both
categories, because that would put the entire distinction inside a single `if`.

## Where the asynchrony lives

The SDK is `anyio`-based; Task Core is synchronous. **Inside the helper**, one
thread owns one event loop which owns one `ClaudeSDKClient`. **In the daemon**,
`HostSession` presents the same small synchronous boundary — `start`, `drain`,
`submit_answer`, `request_cancel`, `close` — so from the adapter's point of view
an out-of-process session and an in-process one are the same object, and every
behaviour worth testing is testable with a double and without a subprocess.

A blocked question does not stall anything. The SDK dispatches control requests
with `spawn_detached`, so the callback awaits an `asyncio.Event` while messages
keep arriving and `interrupt()` keeps working. A cancel wakes it before doing
anything else, so a callback is never left waiting inside a session being torn
down.

The reader stops at the first terminal event, disconnects, and the thread exits.
That is what keeps "no unbounded background task" true rather than intended.

Bounds everywhere: a 90 s start, a 60 s helper-ready wait, a 120 s session start,
a 15 s cancel, a 20 s close, a 20-minute question timeout, at most eight questions
per session, a 6-hour helper lifetime ceiling, a 96 KB protocol line, and a
500-event buffer at each end that drops its oldest rather than growing. A question
nobody answers within the timeout is **declined truthfully** — the agent is told
nobody answered — rather than answered with a guess.

## Cancellation

Task Core remains the authority. `cancel_task` writes `cancellation_requested`
into the history first, then asks the adapter, which sends a `cancel` command
down the pipe to *this* task's own helper, which calls the SDK's own
`interrupt()` on the loop that owns *this* task's client. No signal, no pid, no
process lookup, no name matching — so "cancel cannot reach another task" is
structural rather than checked. The only escalation to a signal is `close()`
stopping the child object its own `Popen` returned: `terminate` then `kill`, as
methods on that object, never `os.kill` and never a pid.

- A cancel closes any question that was open, in the same act, so nobody is left
  looking at an answer box for a task that is stopping.
- A cancel that did not land is a **refusal**, not a claimed `cancelled`. The
  core leaves the task `cancelling`.
- A result that had already arrived **wins** over a cancel that arrives after it,
  because saying a completed task was stopped is the falsehood an audit recovers
  from least well. The request is not lost: it is already in the history.
- A result arriving *after* a cancellation is dropped by the event log's finality
  rule and cannot rewrite the outcome.
- Repeated cancellation is truthful, and an unrelated task is untouched.

## Same-session follow-up (M2I PR3)

### What the SDK actually supports

Three facts, read from the published 0.2.134 source rather than assumed, and the
whole feature rests on them:

| Fact | Where |
|---|---|
| A result frame ends **one turn, not the run** | `_internal/query.py`, in so many words |
| `receive_messages()` keeps yielding past a `ResultMessage` | it breaks only on an `end` or `error` frame |
| `connect()` with **no** prompt never closes stdin | `client.py` spawns `stream_input` — which ends with `end_input()` — only for an `AsyncIterable` prompt |

Cofferdam calls `connect()` with no argument and delivers the prompt with
`query()`, which it already did for the permission-callback reason. That shape
turns out to be exactly the one that leaves the transport open for a second
`query()` later. A follow-up is therefore one more write to a connection that was
never torn down — no reconnect, no `resume`, no second client.

### Turn identity

One task, one provider session, ordered turns. A turn is its own durable row:

| Field | What it is |
|---|---|
| `turn_number` | Cofferdam's, allocated `MAX+1` inside the transaction, from one |
| `provider_session_id` | the conversation this turn happened in |
| `provider_turn_sequence` | the provider's own ordering, kept beside Cofferdam's rather than instead of it |
| `source`, `followup_request_id` | who asked for this turn, and under which request id |
| `started_at`, `completed_at`, `outcome`, `result` | when, how it ended, and the bounded answer |

There is no transcript column, no message list and no payload column, and a
completed turn is **never written again**: the update is guarded on
`completed_at IS NULL`. That guard is what makes "a second turn cannot overwrite
the first turn's evidence" a property of the schema. `tasks.final_result` still
moves on, because it is written with `COALESCE` and always has been — which is
precisely why the turn table exists.

### What `get_result` means

**The latest completed turn's result.** For a terminal task that is also the
final task result, and `task_terminal` says which case a reader is holding. Both
are fields; the payload also carries `result_meaning` in words, so a bridge
author reading one response never has to find this file.

"Completed" means the turn *succeeded*. A task whose first turn answered and was
then cancelled returns that answer, with `outcome: cancelled` and
`task_terminal: true` — the answer is real and readable, and the task was
cancelled, and the response says both rather than picking one.

A terminal task with no successful turn — cancelled before it answered, failed on
the way, interrupted by a restart — returns its outcome and timestamp and no
invented text. A live task with nothing yet returns `task_result_not_ready` (409,
not 404: the task exists).

### The follow-up contract

Allowed only from `ready_for_followup`, with a live session, and no question
open. Refused, with a distinct code for each, when the task is unknown,
cancelled, failed, interrupted, has a pending clarification
(`task_clarification_pending`), has no live session
(`task_session_unavailable`), already has a turn in flight
(`task_followup_in_flight`), belongs to an adapter that does not claim follow-up,
or reuses a `client_request_id` with different content (`task_idempotency_conflict`).

A follow-up from `waiting_for_user` **resumes** the open turn rather than opening
a new one: that turn is running and blocked, and recording two turns for one unit
of provider work would put a second `started_at` on something that never stopped.

The adapter is asked *before* anything is written. A follow-up recorded as
delivered that never reached the session would show somebody their message
accepted while the agent sat idle.

### What the M2I PR3 live spike found

One disposable task on `claude-sandbox`, against a non-production daemon bound to
`127.0.0.1` with a temporary `COFFERDAM_HOME` and a registry containing only that
project. The session was tightened below the shipped profile for the run —
`tools: []` and a USD 0.50 budget — so a tool request or a clarification was
structurally impossible rather than merely unexpected. `PROFILE_MAX_TURNS` was
unchanged and the tightening was reverted afterwards.

| Claim | Observed |
|---|---|
| A turn-ending result leaves the session usable | turn 1 reached `ready_for_followup`, result `Blue.` |
| A follow-up continues the same provider session | one id, byte-identical on **both** turns (not reproduced here: this document does not publish session identifiers, and the rule does not bend for a dead one) |
| One helper, one client | helper PID `163871`, parent = daemon, started before turn 1 and still the only one after turn 2 |
| The second turn has the first turn's context | *"In one sentence, what colour did you just name?"* → *"I named the colour blue."* |
| Turn 1 survives turn 2 | both rows present; turn 1 still `Blue.` |
| `get_result` means what it says | turn 2 while live (`task_terminal: false`, `follow_up_available: true`), the same result after `finish` (`task_terminal: true`, `follow_up_available: false`) |
| Retries do not duplicate a turn | same `client_request_id` → 200 and still two turns; different content → `409 task_idempotency_conflict` |
| No tool, approval or clarification | none in the event stream |
| Nothing leaked | no raw payload, reasoning, transcript, environment value, credential or provider debug field anywhere in the database |
| Nothing changed on disk | the sandbox was byte-for-byte identical and its git tree clean |

**It also found a defect, which is the point of running one.** The adapter emitted
its own "your follow-up was delivered" event *and* the session emitted one when
the turn actually began — two near-identical history lines, the first carrying a
turn number that was already stale because the parent's mirror does not advance
until the helper reports the turn ending. The adapter's event was removed:
`followup_received` and the session's own activity are each true of a different
moment, and a third line between them was neither.

### Restart, and what is not claimed

The live client is in memory inside the helper. If the daemon or the helper is
lost, the conversation is gone: `session_available` reads an empty dictionary and
answers `False`, so the refusal is a consequence of the world rather than a flag
somebody remembered to set. The task becomes `interrupted`, the turn that was
running closes as `interrupted`, and **every earlier completed turn is
untouched** — a task interrupted on its third turn still returns its second
turn's result.

**Cross-process follow-up is unsupported.** `options.resume` exists in the SDK
and Cofferdam pins it to `None`. Using it would need its own evidence — that a
new process resumes a prior session id with context intact and the same
permission boundary — and that evidence does not exist. There is no recovery by
session id anywhere in this build.

## The result foundation

A terminal event produces a provider-neutral `DelegatedResult`: task id, terminal
state, bounded result *or* a failure category with a bounded Cofferdam-worded
summary, provider and session provenance, and a completion timestamp. There is no
`traceback` field and no `exception` field.

`adapter.result_for()` remains in-process memory and is **not** the durable
record. As of PR3 the durable record is the `task_turns` row, written inside the
transaction that moves the task, and `GET /api/tasks/{id}/result` serves a
normalized view of it.

## The phone, and what PR4 found (M2I PR4)

PR2 shipped the clarification routes and PR3 shipped follow-up and `get_result`.
PR4's audit asked the question those three had not: **can somebody actually do
any of this from a phone?** The answer was no, and the reason was entirely on the
client — which is why this section exists as its own thing rather than as a note
on the ones above.

### The gap

The PWA rendered a generic "Your answer" box for any `waiting_for_user` and
posted it to `/followups` — a route the server refuses outright for as long as a
question is open. The two routes had been apart on the wire since PR2 and
together on the screen ever since. From a phone, the headline feature of M2I
could not be used at all.

`web/tasks.js` now reads `GET /api/tasks/{id}/clarifications`, renders the
normalized question with its options, and submits through
`POST /api/tasks/{id}/clarifications/{qid}/answer` with a body of exactly
`answer` and `option_ids`. While a question is open there is no follow-up box,
because a field whose contents the server would refuse is not a field.

**There is still no approval control, and there is no route for one.** The panel
says so where somebody is about to type: answering is *information, not
permission*, and it cannot approve a tool. A test scans `tasks.js` for
`approve`, `approval`, `deny` and `permission_mode` as request keys, control ids
and route segments — matched as those shapes rather than as substrings, so the
panel's own honest copy survives the scan and a control would not.

### Polling, and the phone-shaped hole in it

The panel polls. On a phone the interesting moment is not while it is polling —
it is the ten seconds after somebody unlocks the screen, when the page has been
frozen and the answer on it is stale. `visibilitychange` now triggers **one
read**, not a new timer: `reschedule` remains the only thing that creates an
interval, and a test asserts the foreground refresh costs at most two requests.
Polling stays stopped while hidden.

### Drafts, and why the previous rule was wrong

The panel used to store nothing, and that was right while it had nothing worth
keeping. It stopped being right when the thing not being kept was somebody's
half-written instruction to an agent: iOS discards a backgrounded tab whenever it
likes, and a rule guaranteeing the draft was lost was protecting nothing.

So the blanket ban became a specific one. `localStorage` is used;
`sessionStorage`, cookies and IndexedDB are not — one storage mechanism is
enough and three are three things to audit. Every key is
`cofferdam.taskdraft.<operation>.<task_id>`, so a follow-up draft and a
clarification draft never share a slot and one task's words can never appear in
another's box. Every access goes through the guarded pattern `app.js` learned
from a real device, where the `localStorage` property access *itself* raises
under Private Browsing; a refusal costs the durability and leaves the panel
working.

Drafts are dropped when a task reaches a terminal state, and **every draft is
removed on sign-out** — the most personal content in the product does not
survive it. A restored draft is text, never a message: nothing is submitted
because the app came back to the foreground.

No token, no provider session id and no provider payload is stored. That is
structural rather than reviewed: there is exactly one storage writer, it takes a
task, an operation and a string, and there is no second `setItem` call site
through which anything else could arrive.

### What the phone found, and why it was not cosmetic

The first real-device run reported a small thing: after an accepted follow-up
produced its result, the sent text was still sitting in the box.

It was not small. The draft is deliberately **not** part of the markup — keeping
it out is what stops the form being rebuilt under somebody on every poll — so
clearing the store emptied memory and `localStorage` while the live textarea kept
holding the accepted words. The next render called `captureDraft`, which reads
that node and wrote them straight back.

The draft came back, the request id had been released with it, and the next tap
on Send therefore submitted the same sentence under a **new** key. The server did
the right thing with a new key and an unrecognised message: it opened another
turn. One intended follow-up produced **three provider turns** with three
distinct request ids, consuming real model usage, with nothing on screen to say
so.

The fix is one helper, `clearAcceptedDraft`, which clears the node before the
store and is the only path either accepted submission takes. Text typed while a
request was in flight is newer than the answer and is left alone; a refusal, a
conflict, a timeout or an unreachable workstation still preserve both the words
and the key.

The lesson worth keeping is about the shape of the bug rather than the bug: a
draft that lives in three places is cleared in three places, and a clear that
looks complete because two of them are empty is the kind that reads as correct in
review. The regression tests assert the consequence — a second tap produces no
second turn — and not only the symptom.

### The second defect: the fix could not reach the phone

The narrow recheck of that fix **failed on the phone, and the fix was not the
reason.** It produced three turns again, from a daemon that was serving the
corrected file.

The cause was found without a provider call: the real `tasks.js` was loaded into
a real browser against a stubbed API, once at the pre-fix commit and once at the
fixed one, on a fresh origin. Pre-fix, the box kept its text and a second tap
posted twice under two keys. Fixed, the box emptied and a second tap posted
nothing. The DOM fix was sound; the phone had been running the old file.

Assets were served by Starlette's `StaticFiles` with `ETag` and `Last-Modified`
but **no `Cache-Control` at all**, and a response that says nothing about its own
freshness may be given a heuristic lifetime by the browser. iOS Safari does
exactly that, and both temporary daemons had used the same origin.

So every static asset now carries `Cache-Control: no-cache`. That is *not*
`no-store`: the copy may be kept, it just may not be used without asking, and
with the existing `ETag` the ordinary case is a 304 with no body. Chosen over
versioned asset URLs because versioning nine `<script>` and `<link>` references
needs a build step and fails silently the first time somebody adds a tenth.

**This is a deployment property, not a UI detail.** A frontend change that cannot
be trusted to reach a device cannot be validated on one, and "clear your browser
cache" is not a release mechanism. The tests assert the header on every shell
asset, on the directory index, on a 304, and end to end: an asset is edited on
disk and the next conditional request must answer 200 with the new bytes.

### Idempotency, and the retry that was not one

One module-level slot held the request key for every write, and it was cleared on
*any* response — including a refusal. A refusal is exactly the moment somebody
presses the button again, so the retry carried a fresh key and arrived at the
server as a second, unrelated message.

Keys are now scoped by operation and task, retained across a refusal, and
regenerated only when the words change — which is the rule the server's own
payload-hash binding needs. Retrying a refused follow-up is recognisable as a
retry; editing it and sending is recognisably a different message.

### Caching

Every route whose body carries task content — the detail view, the event stream,
the question list and the result — is served `Cache-Control: no-store`. Not
`no-cache`, which still permits writing the body to disk: for this content that
means somebody's private instruction to an agent sitting in a browser cache
directory after the sign-out that was supposed to remove it. Routes carrying no
task content, such as the adapter list, are deliberately unmarked, and a test
asserts that too — `no-store` everywhere would say nothing about anything.

## Helper ownership and cleanup (M2I PR4)

Three defects, all of the same kind: something Cofferdam owned could outlive the
thing that owned it.

**The adapter's `shutdown` had no caller.** It was implemented in PR1 and never
invoked, so a daemon stopped with a live task left its helper to work out on its
own that its parent was gone. The helper *does* work that out — its loop ends
when stdin closes — but "the child notices" is a weaker guarantee than "the
parent closed it", and only one of the two is bounded. `AdapterRegistry.shutdown`
now asks every adapter in turn from the daemon's lifespan, and one adapter's
failure does not stop the next, because the cost of that coupling is somebody
else's process surviving the shutdown.

**Termination reached the wrong process.** `Popen.terminate` signals the helper
alone, and the helper is not the only process: the SDK starts a Claude CLI inside
the helper's process group. A terminated helper could leave that CLI orphaned
with a live subscription session — the exact leak this milestone names.

So the stop now signals the **group**, under the ownership rule the Claude Code
adapter has enforced since M2G: pid, `/proc` start time and group id recorded at
launch, and **all three** re-verified immediately before every signal. The start
time is the clause that does the work — a pid is a small integer the kernel
reuses, and between recording one and signalling it the helper can exit and
somebody's editor can be given the same number. On a personal workstation that
editor is the thing this codebase exists not to disturb.

Nothing enumerates processes, reads a process name or names a group Cofferdam did
not create. Escalation is graceful-first and bounded at every step — an agent
killed mid-write is one that did not finish writing the file it was editing — and
every branch ends in a `wait`, so no child is left as a zombie.

**The structural guard changed, and the change is worth stating.** `hostclient.py`
used to be held to "call `terminate` and `kill` as methods on the object `Popen`
returned, and never name a pid or a group". That sounds stricter and was, about
the wrong thing: refusing to name a group did not prevent an orphan, it prevented
cleaning one up. What stays forbidden is everything that makes a stop *broad* — a
bare `os.kill` on a pid, `psutil`, `pkill`, `killall`, `pidof`, and any match on
a process name — and a test asserts the identity check appears *before* the
signal in the file.

**A lost helper now says so.** `HostSession.note_lost` also had no call site, so a
helper whose process died produced "ended without producing a result" — which is
equally true of a helper that simply had nothing to say. Those are different facts
and the difference is what an operator reads a task history for. It is not called
for a cancelled session: a task somebody stopped must never be reported as a
transport failure.

## Startup reconciliation (M2I PR4)

`recover_after_restart` was substantively correct and under-covered. The two
states M2I *added* are the two where getting it wrong is least visible, because
both look like "the task is waiting for you" and after a restart there is nothing
on the other end of either.

- **`ready_for_followup` with no live helper becomes `interrupted`.** That state
  renders a box somebody types into; the process that would receive it died with
  the daemon, and there is no reattach.
- **A pending question becomes `interrupted`, and the question is closed as
  superseded** in the same write. Leaving it pending would put a task in the
  "needs you" bucket with an answer box whose only possible outcome is a refusal.
- **Earlier completed turns are untouched.** A restart ended the conversation,
  not the answer: `get_result` still returns the last completed turn's result and
  reports that no follow-up is available.
- **Terminal tasks are never read into the path at all** — same state, same
  result, same timestamp, same number of history rows.
- **It is idempotent.** A crash loop that runs it four times settles one task
  once and writes nothing on the other three passes.

None of this is a limitation being worked around. The adapter does not claim
`recover_after_restart`, and that is what routes every state above to
`interrupted` rather than to `recovery_required` — a state that would promise
somebody a way back.

## The shipped profile, by name (M2I PR4)

There is exactly one profile, it is called **`cofferdam-project-edit-v1`**, and
it is now published as data by `options.describe_profile()` and carried in the
adapter's capability description. A named profile is not a profile *system*:
naming it makes the single shipped set quotable and assertable, and the moment
somebody adds a second name they have to add a selector too — which is the
visible change the constant exists to force.

The values are in [The tool profile](#the-tool-profile) and are unchanged by PR4.
What PR4 added is that they can be read off a running build rather than out of a
document that may describe a different version, and that each is asserted against
a written-out literal rather than against itself.

**On the filesystem boundary, the honest version.** `cwd` plus an empty
`add_dirs` is what the CLI is *told*. That is a configuration boundary enforced by
the agent's own tool implementations, not a kernel one. Cofferdam has **not**
verified that a sufficiently determined `Read` of an absolute path outside the
project root is refused, and this build does not claim it is. What it does claim
is narrower and true: there is no shell in the session, so there is no general
execution primitive to escape *with*, and every path Cofferdam itself resolves
comes from the project registry. A test asserts the published limitations say
"not a kernel sandbox" and "does not claim", and fails if the word "sandboxed"
appears.

The child environment allowlist is unchanged and re-asserted: a complete
code-owned environment built in `hostenv`, replacing rather than merging with the
daemon's own, with no Cofferdam credential and no other provider's in it.

## Unsupported clarification variants (M2I PR4)

Three shapes remain unobserved, listed in `SCHEMA_EVIDENCE_OUTSTANDING`:

- more than one question in a single tool input;
- `multiSelect: true` — a genuinely multiple-choice question;
- a question offering no options, answered as free text.

The reader already handles them conservatively, and the phone now **says so on
the screen**: a question whose shape this build has not verified is labelled as
such rather than presented as verified. What is missing is evidence that the
provider produces them; a later spike that sees one moves it out of that tuple
rather than widening any parsing.

## Tests

`tests/test_delegated_events.py`, `tests/test_agent_sdk_adapter.py` and
`tests/test_task_clarifications.py`, plus sanitized doubles in
`tests/_agent_sdk_doubles.py` whose class and attribute names were read from the
published distribution — a double that got one wrong would let the suite pass
while the real stream produced nothing.

PR3 adds `tests/test_task_followups.py` and two classes in the SDK suite. The
one worth naming is `SdkSessionTurnTests`, which drives the **real**
`SdkSession` — its thread, its loop, its receive loop, its between-turn park and
its session-identity check — against a scripted async client, so the multi-turn
code that ships is the code under test rather than a double of it. What that
still cannot prove is that the real SDK behaves the way the scripted client
does; that reading came from the source and the live spike is what settles it.

PR2 adds 127 focused tests across the environment boundary, the conservative
schema reader, the bounded observer, clarification/approval separation, the
lifecycle, provenance, answer encoding, the helper protocol, same-session
routing, and the two authenticated routes. Three structural guards were made
*narrower* rather than looser: the package may now spawn exactly one process,
from exactly one file, with an argument vector and environment asserted from the
syntax tree — and a new guard fails if any module in the package ever passes
`os.environ` to anything.

No test calls Anthropic, consumes model usage, requires a login, touches the
network, starts a subprocess, reads a transcript, modifies the live registry or
restarts anything.

The live spike settled three things the tests could not: that `can_use_tool`
fires for `AskUserQuestion`, that the input arrives in the shape this build
reads, and that the real helper drives a real SDK through a complete round trip.
`OBSERVED_LIVE_SHAPE` in the test file reconstructs the observed structure with
invented content, so the reader is now tested against the real field names.

PR4 adds focused tests in four places. In the SDK suite: the process identity
rule against **real short-lived processes** — the one property a double cannot
prove — every way a helper can end, restart reconciliation across all five
non-terminal states, and the named profile. In `tests/test_tasks_pwa.py` and
`tests/tasks_harness.js`: the clarification workflow, draft survival across a
genuine reload, request-id retention, the foreground refresh, and result
retrieval. In `tests/test_task_clarifications.py`: the `no-store` header on every
task-content route, and its deliberate absence on a route that carries none.

The harness's `localStorage` double lives **outside** the sandbox that runs
`tasks.js`, so a "reload" builds entirely fresh module state and only what was
genuinely written down crosses over. A per-sandbox store could not tell "the
draft was saved" from "the draft was still in a variable".

What the tests still do **not** prove, said plainly:

- the three unobserved variants in `SCHEMA_EVIDENCE_OUTSTANDING`;
- anything about production. Every run was on a non-production daemon, and no
  claim is made about the live service.

## The real-phone validation

Three supervised runs, each on a temporary tailnet-only daemon with its own
`COFFERDAM_HOME`, one-project registry and token, from a **disposable worktree**
so the PR branch stayed byte-clean. The session was narrowed below the shipped
profile for every run and restored by deleting the worktree.

**Run 1** exercised the full workflow — structured question, answer through the
clarification route, drafts surviving a locked screen, foreground refresh — and
found the accepted-follow-up draft defect. Sixteen checks passed; one failed, and
the failure produced three provider turns from one message.

**Run 2** rechecked the fix and failed again with three turns, from a daemon that
was serving the corrected file. That is what exposed the stale-asset defect.

**Run 3**, on `935d455` with a visible `validation build 935d455` marker and
every asset carrying `Cache-Control: no-cache`, passed:

| | |
|---|---|
| Tasks | 1 |
| Turns | 2 — `Ready.` then `Atlas.` |
| Accepted follow-ups | 1, under **one** request id |
| Provider session across both turns | one |
| Tool or clarification events | none |
| Follow-up field after acceptance | empty |
| After reload | still empty, marker still shown |
| Send on an empty field | local refusal, no request |

**The middle run is the one worth keeping in mind.** A fix that is correct in the
repository, correct under test and correct in a browser is still not a fix a
device has received, and for two runs nobody could tell the difference from the
outside. The build marker exists because of it.

No token, provider session id, raw payload, transcript, hidden reasoning or
environment value was recorded from any run. Production's pid, start time, start
ticks, drop-in hash, registry hash and SDK absence were compared before and after
each and were unchanged throughout.

## Can M2I close?

Yes — the milestone's own goal is met, and the distinction below is what that
does and does not mean.

**What is finished.** The transport, the structured question channel, same-session
follow-up, the result boundary, the phone surface for all three, helper ownership
and cleanup, startup reconciliation, and asset revalidation — each with automated
coverage that calls nothing, and all of it exercised on a real phone against a
temporary non-production daemon.

**What is still outstanding, and is not M2I's to close.** The three unobserved
question variants in `SCHEMA_EVIDENCE_OUTSTANDING`; a `multiSelect` question and a
free-text-only question have never been produced by a real session, so the panel
marks any unverified shape rather than presenting it as verified. Parity with the
Claude Code adapter is a separate judgement the roadmap's retirement rule governs,
and nothing here asks for that adapter to be retired.

**What closing M2I would not mean.** It would not deploy this adapter. The Agent
SDK adapter stays **off by default and undeployed**: it is a separate host flag,
no shipped unit or drop-in enables it, the installer does not require the extra,
and the production venv has never had `claude_agent_sdk` in it. The **Claude Code
adapter remains the production transport and the fallback**, unchanged by this
PR, and the roadmap's retirement rule — it goes only after verified parity —
still stands.

## Rollback

Revert the PR. The adapter is off by default, the Claude Code adapter is
unchanged and remains available, and no production unit, registry entry or
drop-in was touched.

PR4 adds two things a revert touches outside this package, and both are safe to
take back: the `no-store` headers on the task-content routes, and the draft keys
the PWA writes under `cofferdam.taskdraft.`. Reverting leaves those keys behind
in a browser that had used the build; they are removed on the next sign-out,
which is the same guarantee they had while the feature was present.

The one thing a revert does not undo is the **schema version**, which PR2 moved
from 1 to 2 by adding `task_clarifications`. That is survivable in the direction
that matters: the change is additive, no existing table moved or changed type, and
an older build opening the database finds every table it knows about exactly as it
left them. It will still *refuse* to open it, deliberately — a build that cannot
see pending questions should not be quietly answering tasks that have them — so a
rollback that must reopen an upgraded database needs the version row set back to
`1` by hand.
