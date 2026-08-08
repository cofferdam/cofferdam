# The Claude Agent SDK adapter — M2I PR1 foundation

A second Lane B transport to the same agent, on the same provider-neutral
[Agent Task Core](AGENT_TASK_CORE.md). Where the
[Claude Code adapter](CLAUDE_CODE_ADAPTER.md) parses a CLI's stream-json on
stdout, this one drives the official **Claude Agent SDK** and receives typed
messages — which is what makes a structured question channel possible at all.

This document describes what was built in **M2I PR1**, and is deliberately clear
about what was not.

> **This adapter is off by default, is not deployed, and has not been run
> against Anthropic from this repository.** Everything below is evidenced by the
> published SDK distribution and by automated tests that call nothing.

## What is *not* here

Stated up front, because a reader's first question about a second agent
transport is what it cannot do and what it does not yet replace.

- **It does not replace the Claude Code adapter.** Both may be registered, they
  have different ids, and the CLI adapter remains the fallback and the only one
  validated live from a phone against this host. `ROADMAP.md` holds the
  retirement rule: the CLI adapter goes only after verified parity.
- **No production change.** No systemd unit, drop-in, installer or registry file
  in this repository enables it, and none was edited by this PR.
- **No question round trip.** A clarification can be *represented* and
  *recorded*; there is no answer route, no PWA question UI, and no way to reply.
  That is M2I PR2.
- **No follow-up.** A task runs one turn and reports its result. The seam is in
  place — the provider session id is preserved and `send_followup` refuses
  truthfully — but the flow is not implemented and the adapter does not claim
  the capability.
- **No tool approval from a phone, ever.** Not "not yet": a permission request is
  denied by a code-owned handler and recorded. See
  [Clarification is not approval](#clarification-is-not-approval).
- **No `get_result` route.** The provider-neutral result *shape* exists and is
  produced; nothing serves it. Claiming otherwise would be claiming M2I.5.
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
`AskUserQuestion` type and no schema for one — that tool belongs to the CLI. The
normalizer therefore recognises a question tool *conservatively*: a clarification
is produced only when the tool input carries an unmistakable question string, and
anything else degrades to ordinary tool activity rather than inventing a
question. It is unreachable in this build anyway, because the question tool is
not in the running tool profile. Verifying the schema is PR2's first job.

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

The safety boundary this PR exists to establish.

|  | Clarification | Tool approval |
|---|---|---|
| What is asked | information or a choice | permission to act |
| Carries | a question, bounded options | a tool name, a coarse category |
| Cannot carry | any tool field | any question or option field |
| Waiting reason | `clarification` | `approval` |
| May be answered remotely | eventually, from the PWA or a Custom GPT | **never** |

They are two dataclasses with disjoint required fields and disjoint serialized
shapes. Each `from_dict` refuses three ways: a wrong `category`, the *presence of
any of the other's fields* even when the category looks right, and a payload with
nothing usable in it. An event cannot hold both, and a request cannot be attached
to an unrelated kind. All six refusals are tested in both directions.

In this foundation the SDK's permission callback is a code-owned handler that
**denies and records**. It is handed the tool's input — the command, the path,
the arguments — and reads only the name; that material is exactly what makes
approvals worth keeping on a trusted surface, and copying it into an event a
phone renders would defeat the arrangement. The adapter declares
`approvals=False`, which is the honest answer.

Neither kind moves the task into `waiting_for_user` yet, and that is a refusal
rather than an omission. An approval is not a wait — Cofferdam denied it and the
agent carries on — so reporting "NEEDS YOU" would be false. A clarification
*would* be a wait, but Task Core's graph has no `waiting_for_user → completed`
edge on purpose, so a task parked there with no answer channel could never reach
a terminal state again. Both are recorded truthfully in the history and the state
is left alone until the PR that builds the channel.

## The tool profile

Identical to the Claude Code adapter's, and the sameness is the point: two
transports for one policy, and a difference between them would mean switching
transport quietly changed what the agent may do. A test asserts they match.

| Option | Value | Why |
|---|---|---|
| `tools` | `Read, Write, Edit, Glob, Grep` | **No Bash.** A shell inside an approved root is still a general shell on the workstation, reachable by writing English into a phone. |
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

### The environment difference

The one place this adapter is weaker than the CLI one, stated rather than papered
over.

The Claude Code adapter calls `Popen` itself and passes a thirteen-name
allowlist, so a variable reaches the child only when somebody added it on
purpose. The Agent SDK offers no equivalent: `options.env` is layered *over* the
daemon's own environment, so the child inherits what the daemon has.

Bounded by three facts. The daemon's environment is host-owned — the systemd unit
and an optional `EnvironmentFile` — and nothing a client sends reaches it. The
four forced overrides are applied last and win. And Cofferdam's own
secret-bearing variable names are explicitly blanked in the child.

Not blanked: the `ANTHROPIC_*` family. Emptying those would change how the agent
authenticates, and Cofferdam has not verified what an empty value does to the
sign-in path — guessing there could break authentication or move spending to a
different account. Narrowing the inherited environment properly needs a custom
SDK `Transport`, which is real work and belongs to a later PR.

## Where the asynchrony lives

The SDK is `anyio`-based; Task Core is synchronous. One thread per task owns one
event loop which owns one `ClaudeSDKClient`. The adapter is written against a
small synchronous boundary — `start`, `drain`, `request_cancel`, `close` — so
every behaviour worth testing is testable with a double and without a subprocess.

The reader stops at the first terminal event, disconnects, and the thread exits.
That is what keeps "no unbounded background task" true rather than intended, and
it is also why there is no same-session follow-up yet: keeping a session alive
for another turn is a different lifetime with different failure modes.

Bounds everywhere: a 90 s start, a 15 s cancel, a 20 s close, and a 500-event
buffer that drops its oldest rather than growing.

## Cancellation

Task Core remains the authority. `cancel_task` writes `cancellation_requested`
into the history first, then asks the adapter, which calls the SDK's own
`interrupt()` on the loop that owns *this* task's client. No signal, no pid, no
process lookup, no name matching — so "cancel cannot reach another task" is
structural rather than checked.

- A cancel that did not land is a **refusal**, not a claimed `cancelled`. The
  core leaves the task `cancelling`.
- A result that had already arrived **wins** over a cancel that arrives after it,
  because saying a completed task was stopped is the falsehood an audit recovers
  from least well. The request is not lost: it is already in the history.
- A result arriving *after* a cancellation is dropped by the event log's finality
  rule and cannot rewrite the outcome.
- Repeated cancellation is truthful, and an unrelated task is untouched.

## The result foundation

A terminal event produces a provider-neutral `DelegatedResult`: task id, terminal
state, bounded result *or* a failure category with a bounded Cofferdam-worded
summary, provider and session provenance, and a completion timestamp. There is no
`traceback` field and no `exception` field.

**No route serves it.** `adapter.result_for()` is in-process memory, lost on
restart; the authoritative record of a finished task remains Task Core's own row.
What exists is the boundary, produced now rather than invented later at the edge.

## Tests

`tests/test_delegated_events.py` and `tests/test_agent_sdk_adapter.py`, plus
sanitized doubles in `tests/_agent_sdk_doubles.py` whose class and attribute
names were read from the published distribution — a double that got one wrong
would let the suite pass while the real stream produced nothing.

No test calls Anthropic, consumes model usage, requires a login, touches the
network, starts a subprocess, reads a transcript, modifies the live registry or
restarts anything.

What the tests do **not** prove, said plainly: that the real session driver works
against a real SDK. That is evidenced by the published source, and would be
evidenced further by a supervised live spike recorded in a pull request.

## Rollback

Revert the PR. The adapter is off by default, the Claude Code adapter is
unchanged and remains available, and no production unit, registry entry or
database migration was touched.
