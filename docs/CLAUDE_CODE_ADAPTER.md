# The Claude Code adapter

The first real agent adapter on top of the provider-neutral
[Agent Task Core](AGENT_TASK_CORE.md). It lets somebody holding a phone pick an
approved project, pick Claude Code, write a bounded prompt, watch truthful
progress, read the result, send a follow-up to the same Claude session, and
cancel that one task — without Cofferdam ever exposing a shell.

Read this first, because it is the sentence the rest of the document defends:

> **A prompt is content for Claude Code. It is not an operating-system command,
> and there is no field anywhere in this milestone that turns it into one.**

## What is *not* here

Stated up front, because a reader's first question about an agent that runs
processes on a personal machine is what it cannot do.

- **No general shell.** There is no route, body field, query parameter or
  registry key that accepts an executable, an argv, a shell string, an
  environment variable, a working directory, a CLI flag, a permission mode, a
  tool list, an MCP configuration, a plugin path, a callback URL, a process id
  or a unit name. Every one of those is server-owned and lives in source.
- **No raw terminal.** The default task view is a task view. Bounded normalized
  events, not a byte stream.
- **No auto-resume.** A Claude task that was running when the daemon restarted
  becomes `interrupted`. Nothing is reattached, because nothing was observed.
- **No hidden reasoning.** Thinking blocks are not requested, not parsed, not
  stored and not shown. The only thing counted is a token estimate, and only to
  say the task is still working.
- **No secrets through the task channel.** Passwords, one-time codes, passkeys
  and tokens must never be typed into a prompt or a follow-up. When Claude Code
  needs authentication the answer is a local action on the workstation, and the
  PWA says so instead of showing a field.

## The installed CLI, verified

Everything below was read off the workstation during development rather than
recalled. Where the documentation and the installed `--help` disagreed, the
installed binary won.

| Fact | Value |
| --- | --- |
| Executable | `/home/nrgis/.local/bin/claude` |
| Resolved install | `/home/nrgis/.local/share/claude/versions/2.1.221` |
| Version | `2.1.221 (Claude Code)` |
| Authentication method in use | `claude.ai` subscription login, first-party |

### Features confirmed present in 2.1.221

- `-p` / `--print` — non-interactive. Reads a prompt from **piped stdin**, so
  the prompt never has to appear in argv.
- `--output-format stream-json` — newline-delimited JSON events, incremental.
- `--input-format stream-json` — *realtime streaming input*. This is the
  feature the whole session architecture rests on: one process accepts more
  than one user turn.
- `--session-id <uuid>` — the caller chooses the session identifier.
- `--resume`, `--continue`, `--fork-session` — session resumption.
- `--permission-mode` with the closed set `acceptEdits`, `auto`,
  `bypassPermissions`, `manual`, `dontAsk`, `plan`.
- `--tools` — restrict which built-in tools exist at all.
- `--allowedTools` / `--disallowedTools` — permission rules.
- `--strict-mcp-config` — ignore every MCP configuration not passed explicitly.
- `--setting-sources` — choose which settings files load.
- `--max-turns`, `--max-budget-usd` — bounded runs.
- `claude auth status --json` — a fixed, read-only authentication probe.
- SIGTERM aborts the in-progress turn and exits **143**.
- Exit `0` on success; non-zero on failure, including "not authenticated".

### Features deliberately not used

`--dangerously-skip-permissions` and `--allow-dangerously-skip-permissions` are
never passed, and a test asserts the strings are absent from the built argv.
`--bg`, `--cloud`, `--worktree`, `--tmux`, `--ide`, `--chrome`, `--plugin-dir`,
`--plugin-url`, `--mcp-config`, `--agents` and `--json-schema` are not used.
`--include-partial-messages` is not used: token-level deltas would be a byte
stream in a task view, which is the thing this milestone refuses to build.

### What was measured, not assumed

Two probes were run against the disposable sandbox project. Their results are
the evidence for the architecture below.

**Probe 1 — two turns, one process.** A single `claude -p` with
`--input-format stream-json --output-format stream-json` accepted an initial
user message, produced a `result` frame, then accepted a *second* user message
on the same stdin and produced a second `result` frame carrying the same
`session_id`. The process stayed alive between turns and exited `0` when stdin
was closed. The prompt text appeared in no argv element, and a Turkish prompt
(`ğüşiöç İ`) round-tripped intact.

**Probe 2 — containment and cancellation.** With `--tools "Read,Glob"`, a
prompt asking Claude to run a shell command produced no shell execution and no
approval prompt: Claude reported that no Bash tool existed in the session, and
`permission_denials` was empty. SIGTERM to the process group ended the run in
0.4 s with exit code 143.

### Observed frame types

`system` (subtypes `init`, `thinking_tokens`, `api_retry`, `plugin_install`),
`rate_limit_event`, `assistant`, `user` (carrying tool results), and `result`
(subtypes `success` and error subtypes). A `system/init` frame is re-emitted at
the **start of every turn**, not only once per process — the parser treats a
repeat as a turn boundary rather than as a new session.

The `result` frame carries `is_error`, `subtype`, `result`, `session_id`,
`stop_reason`, `terminal_reason`, `num_turns`, `permission_denials`, `usage`
and cost fields. Only `is_error`, `subtype`, `result`, `session_id`,
`stop_reason` and `permission_denials` are read; the rest are ignored.

## The architecture decision

**One bounded long-lived structured process per task.**

`claude -p --input-format stream-json --output-format stream-json`, launched
once when the task starts, held open for the life of the task, fed one JSON user
message per turn on stdin, read incrementally on stdout by a bounded parser in a
reader thread.

### Why this, and not one-shot turns with `--resume`

`--resume` genuinely works in 2.1.221 and would have been the safer-looking
choice. It was rejected on three grounds, in this order:

1. **Follow-up continuity is proven rather than hoped for.** With one process,
   the follow-up goes to a session that is *demonstrably* the same one: the
   file descriptor is still open and the `result` frames carry the same
   `session_id`. With `--resume`, continuity depends on a session store on disk
   that Cofferdam does not own and cannot verify.
2. **Cancellation targets something real.** A live process has a pid, a start
   time and a process group, all of which can be verified before a signal is
   sent. A one-shot design spends most of a task's life with no process at all,
   so "cancel" would have to mean "remember not to start the next turn", which
   is not the same promise.
3. **Restart honesty is free.** The service unit runs with
   `KillMode=control-group`, so a restart takes the whole cgroup with it. The
   process dies, the reader thread's stream ends, and Task Core marks the task
   `interrupted` — which is true. A `--resume` design would leave a session on
   disk that *could* be resumed, which is precisely the temptation this
   milestone is supposed to refuse.

The cost is accepted honestly: a long-lived process holds memory and a
subscription session for the life of the task, which is why concurrency is
capped at one and why every task carries bounded turn, byte and time limits.

### What happens when the daemon restarts

The Claude process is a child of the service, inside its cgroup, with
`KillMode=control-group` and `KillSignal=15`. systemd terminates the cgroup, the
child receives SIGTERM and exits 143. On start-up
`TaskService.recover_after_restart` finds a row that says `running`, sees that
the adapter declares `recover_after_restart: false`, and moves it to
`interrupted` with an event saying the service restarted underneath it. Prior
output and any final evidence already recorded are preserved. Terminal tasks are
not touched.

The adapter's `recover_after_restart` capability is `false` and stays `false`
until a complete safe design exists and is proven. It is not "possible later"
dressed up as true today.

### Where the asynchrony lives

Task Core calls an adapter synchronously and takes its answer as a report. A
Claude run takes minutes, so `start()` must not block for it. The split:

- `start()` launches the process, waits only for **process evidence** — a live
  pid whose `system/init` frame arrived carrying the session id Cofferdam
  chose — and returns. A launch that never produces that evidence is a failure,
  not a `running` task.
- A reader thread consumes the stream and accumulates bounded normalized state.
- `inspect()` — declared in the merged protocol and previously unwired — hands
  that accumulated state back to the core on read, which applies it through the
  same transition graph every other report goes through.

Wiring `inspect()` is the one Task Core change this milestone makes. It is
generic, it names nothing Claude-specific, and it completes a method the
foundation already declared rather than redesigning anything.

## The adapter boundary

Every line of Claude-specific knowledge in Cofferdam lives under
`cofferdam/workstation/tasks/adapters/claude_code/`. Four modules:

| Module | Owns |
| --- | --- |
| `cli.py` | The executable, the argv template, the permission profile, the environment allowlist, the auth probe |
| `frames.py` | The bounded parser: untrusted NDJSON in, a closed set of normalized records out |
| `process.py` | One process, its identity, its stream, its cancellation |
| `evidence.py` | The fixed Git observations that turn a claim into something Cofferdam saw |

Task Core imports none of it. Two tests enforce that: no module in
`tasks/` outside `adapters/` may name Claude, and no module outside the package
may construct `ClaudeCodeAdapter` except the registry.

### The two changes made to Task Core

Both are generic, both name nothing Claude-specific, and both complete something
the foundation declared and left unimplemented because its only adapter needed
neither.

**`TaskService.refresh_task`.** The protocol declared `inspect()` — "what the
adapter believes is happening, asked fresh" — and nothing ever called it. That
was fine for an adapter that finishes inside `start()`. It is not workable for
one whose work happens over minutes in a process. The task detail route now
calls `refresh_task`, which asks the adapter and applies the answer through the
same transition graph as every other report. It is deliberately the *only*
mechanism: no callback from an adapter into the service, no queue, no background
sweep, because each would be a second path that writes task state.

**`AdapterOutcome.observations`.** `AdapterEvent`'s docstring promised that
evidence is stamped `adapter_reported` "unless the core observed the thing
itself", and the core had no way to express the exception. Now it does. The rule
for events does not bend — anything in `events[].evidence` came out of the thing
being adapted and stays a claim forever. `observations` carries the result of an
operation *Cofferdam* ran, and the core still checks each `source` against
`VERIFIED_EVIDENCE_SOURCES` and demotes anything else. An adapter cannot launder
a claim by moving it into that field; it can only fail to be believed.

## Enablement

Off by default. Three host-owned mechanisms, exactly as for the validation
adapter:

```bash
python -m cofferdam.workstation --enable-claude-code-adapter
```

or `"enable_claude_code_adapter": true` in `$COFFERDAM_HOME/config/config.json`,
or `COFFERDAM_ENABLE_CLAUDE_CODE_ADAPTER=1` in the unit's environment.

When the flag is absent the object is never constructed, so there is nothing for
a request to reach even if it names the id correctly. `build_registry` takes two
booleans and nothing else — no path, no class, no module name. The daemon
announces the adapter on every start, not only when the flag was typed.

**A project must also permit it.** `task-projects.json` lists which adapters may
run in each project, and an empty list means *none*:

```json
{
  "projects": [
    {
      "project_id": "claude-sandbox",
      "display_name": "Claude adapter sandbox",
      "root": "/home/nrgis/cofferdam/validation/claude-adapter-sandbox",
      "adapters": ["claude-code"]
    }
  ]
}
```

Both gates must be open. An enabled adapter with no project permitting it can
run nothing.

## Project authority

A task names a `project_id`. The server resolves it, and re-verifies the root
**immediately before launch** — every path component `lstat`-ed for symlinks,
then the resolved real path compared against the configured one. A root deleted,
replaced by a file, or turned into a symlink between service start and the task
is a refusal.

There is no request field anywhere for a path, a working directory, or a
directory name, and the project's root is never published to a client: a phone
picks a project by name and never learns where it lives on disk.

## The permission model

One profile, code-owned, no way to select a second.

| Setting | Value | Why |
| --- | --- | --- |
| `--tools` | `Read,Write,Edit,Glob,Grep` | **Bash is absent.** This is the most important line in the package. |
| `--permission-mode` | `acceptEdits` | Writes inside the root without a prompt nobody could answer headless. Does not grant shell. |
| `--strict-mcp-config` | set, with no `--mcp-config` | Every MCP server configured anywhere on this machine is ignored. |
| `--setting-sources` | `""` | No user, project or local settings file can widen the profile. |
| `--disable-slash-commands` | set | A prompt starting `/foo` is text, not an invocation. |
| `--no-session-persistence` | set | Nothing is left on disk for a later `--resume` to reattach to. |
| `--max-turns` / `--max-budget-usd` | `24` / `2.00` | A runaway agent stops on its own. |

Removing Bash rather than gating it behind approval is the whole containment
argument. A Bash tool inside an approved root is still a general shell on the
workstation, reachable by writing English into a phone. Probe 2 confirmed the
CLI reports "no Bash tool is available" rather than prompting, so the refusal
happens before any decision has to be made.

`--dangerously-skip-permissions` and its `--allow-` variant are never passed, and
a test asserts every name in `FORBIDDEN_FLAGS` is absent from the built argv.

Cofferdam does not write or modify any Claude settings file, project policy file,
or global configuration, at startup or ever.

### Approvals: what this version cannot do

`approvals` is **`False`**, and that is a statement about today rather than a
placeholder. This adapter cannot answer a permission request: there is no
channel for it, and inventing one that auto-approves would be the single worst
thing in this milestone.

When the CLI reports a `permission_denials` entry, the task becomes
`waiting_for_user` with reason `approval`, naming only the **tool** that was
refused. The tool *input* in the same record — the command string, the path — is
never published. Resolving it means a person deciding what to do, either by
sending a different follow-up or by doing the thing at the workstation.

## Authentication

Detected with one fixed invocation, `claude auth status --json`, run without a
shell and with the same allowlisted environment as a task. Cofferdam never opens
a credential file, a keychain, a browser profile or a token store — this probe is
the only thing in the package that asks about authentication at all.

Exactly two fields are read: `loggedIn` and `authMethod`. The same document
carries an email address and organisation identifiers; none is assigned to
anything, and `AuthStatus.__slots__` is `("logged_in", "method", "probe_failed")`
so there is no attribute for a later refactor to log by accident.

Not signed in →`waiting_for_user(authentication)`, no process started, and the
PWA shows a sentence rather than a field:

> **Cofferdam will not ask you for this here.** Finish this on the workstation
> itself. Never type a password, one-time code, passkey or token into a task.

A probe that could not *run* is not treated as "you are signed out" — that would
send somebody to re-authenticate an account that was fine. Retry is explicit:
start a new task. Nothing loops.

### The environment boundary

The child gets an allowlist of thirteen names plus four forced values, built by
*selection* rather than by copy-and-delete — a denylist would ship every variable
somebody adds to the unit file next year.

`HOME` is the load-bearing entry. The installed CLI authenticates with a
`claude.ai` subscription login whose credentials live under the user's home
directory. Cofferdam does not read them, does not know their format, and does not
name their path; it passes `HOME` and lets the CLI do its own thing. **Cofferdam
grants reachability, never possession.** No `ANTHROPIC_*` variable, no key, no
token, and nothing from a request.

## Concurrency

One active Claude task, and the policy is **truthful refusal, not silent
queueing**. A second task is created, fails immediately, and says why: "another
Claude Code task is already running. Cofferdam runs one at a time — wait for it
or cancel it."

Each task holds a live process and a subscription session for its whole
lifetime, which is why the number is one. It is a constant in source; no request
carries one, and no response invites a client to change it. A failed launch
releases the slot; a cancellation releases it only after the process was
*observed* stopped.

## Process identity and cancellation

A pid is a small integer the kernel reuses, and on a personal workstation the
program that gets the recycled number is somebody's editor. So a run is four
facts together:

- the pid
- the process **start time**, `/proc/<pid>/stat` field 22 — assigned by the
  kernel at exec, and the one a recycled pid cannot reproduce
- the process group id, a group Cofferdam created with `start_new_session=True`
- the adapter run id, so a stale reader thread from an earlier run cannot be
  mistaken for the current one

`still_ours()` requires all four. It is re-checked before **every** signal, not
once at the top — between SIGTERM and SIGKILL a process can exit and its pid be
reused, and a SIGKILL sent on the strength of a ten-second-old check is a SIGKILL
sent to whatever holds that pid now.

Escalation: SIGTERM to the process group, 10 s, then SIGKILL, 5 s. SIGTERM first
because the CLI documents it — the turn aborts, `SessionEnd` hooks run, exit 143
— and because a process killed mid-write is one that did not finish writing the
file it was editing. Probe 2 measured 0.4 s.

Never `pkill`, `killall`, `pidof`, `pgrep`, process-name matching, or a pid from
a client. If identity is lost the result is `identity_lost` and **nothing is
sent**. If the process will not stop, the task stays `cancelling` rather than
being promoted to `cancelled` — claiming a task stopped because stopping it was
requested is the false success this whole design refuses to produce.

Cancelling one task cannot affect another task, the daemon, Spotify, Opera, or a
Claude session somebody is running in their own terminal. A test starts a
bystander process with the same program name in a different process group and
asserts it survives.

## Restart and orphans

The unit runs with `KillMode=control-group` and `KillSignal=15`, so a restart
takes the whole cgroup: the child gets SIGTERM and exits 143. On start-up
`recover_after_restart` finds the row that says `running`, sees the adapter
declares `recover_after_restart: false`, and moves it to `interrupted` with an
event saying the service restarted underneath it.

Nothing is resumed and nothing is adopted. `--no-session-persistence` means there
is not even a session on disk to be tempted by. Prior output and any evidence
already recorded are preserved; terminal tasks are untouched.

The adapter also stops its own children on `shutdown()`, so a clean stop does not
depend on the cgroup alone.

## The parser

Four properties, enforced in `frames.py` rather than downstream:

**Bounded per line.** A line over 256 KB is not read into memory and then
discarded — it stops being buffered as it arrives and the rest is drained to the
newline. The frame is refused, the stream stays synchronised, memory does not
move.

**Bounded in total.** 8 MB across the life of the task, after which the reader
stops and the task says it produced more output than Cofferdam keeps.

**Recognised shapes only.** Every field read is named in source. An unknown
`type` is counted, never published. `StreamRecord` has five fields —
`kind, text, detail, tool, is_error` — and none of them can hold a dictionary, so
a CLI payload cannot reach Task Core even by accident.

**Text is text.** ANSI/OSC escapes, C0/C1 controls and bidirectional overrides
are removed; newlines and tabs survive. The PWA renders with `textContent`, so
this is the second layer rather than the only one.

Thinking and `redacted_thinking` blocks are skipped without a branch. The only
thing read from `system/thinking_tokens` is that the task is still working.
Tool results report *that* a tool finished and whether it errored, never the
body — a tool result can be an entire file. `permission_denials` yields tool
names only.

A `system/init` frame arrives at the start of **every turn**, not once per
process; a repeat carrying a different session id is recorded as an error rather
than overwriting what Cofferdam launched.

## Sessions and follow-up

Cofferdam generates the session UUID and passes it with `--session-id`, then
verifies the CLI reports the same one back in `system/init`. A mismatch is a
refused launch, not an adoption.

A follow-up is delivered to the process this task already owns, found by this
task's own id. There is no session parameter on any route, any service method,
or `send_followup` itself, and no field on any request body — the API accepts
exactly five keys: `project_id`, `adapter_id`, `prompt`, `client_request_id`,
`title`.

**While a turn is running, a follow-up is refused rather than queued.** Writing a
second user message into the stream mid-turn would inject it at a point nobody
chose. The refusal names the reason: "Claude is still working on the previous
message."

Idempotency is Task Core's, and it is checked *before* the state checks, so a
mobile retry of an answer that already landed returns the task rather than
"already finished". The `followup_received` event records that one arrived and
how long it was, never what it said.

## Privacy and audit

The package contains no logging call at all — no `logging`, no `logger`, no
`print(`. That is stronger than filtering, and a test enforces it across every
file including this package.

Prompts, follow-ups and results live on the task row and in `meaningful_output`
events, which is where the authenticated detail view reads them. They do not
appear in: the audit trail, any lifecycle event, `journalctl`, a URL, a process
argv, an environment variable, a unit name, a task id, the browser console, or a
Git commit message Cofferdam writes.

The audit hook's *signature* has no parameter for content, which makes that a
property of the function rather than a habit every caller has to keep. Audit
records carry ids and outcome words only.

`ensure_ascii=True` on the stdin JSON means a Turkish prompt crosses the pipe as
ASCII escapes and is reconstituted by the CLI's own reader, so it cannot be
mangled by whatever encoding the child turns out to have.

## Evidence

| What happened | Recorded as |
| --- | --- |
| Claude says "I edited `sandbox.py`" | `adapter_reported` — a claim, forever |
| Cofferdam's `Popen` returned a pid | `os_observed` |
| Cofferdam ran `git status` and saw a changed path | `git_observed` |
| Cofferdam ran `git rev-parse HEAD` and saw a commit | `git_observed` |

Four probes, all constants in source, all `shell=False` with a fixed `cwd` and a
minimal fixed environment:

```
git rev-parse --is-inside-work-tree
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --porcelain
```

The membership check in `_run` refuses anything not in that tuple, even from a
caller inside the package, so a future edit that builds a command from a variable
fails immediately rather than working quietly. A path from `git status` that does
not stay inside the root after resolution is dropped rather than reported.

**No client-supplied test command is ever run**, and the project registry has no
field for one — `command`, `cmd`, `script`, `exec`, `argv` and `env` are refused
by name when a project entry is loaded.

## The task view is not a terminal

The default view shows current activity, latest meaningful output, elapsed time,
the final result, the failure or interruption reason, an evidence summary, and
the adapter's stated limitations. Follow-up and cancel appear only when the
adapter's capabilities say they can work.

It does not show raw bytes, ANSI styling, the environment, hidden reasoning,
credential data, or unbounded tool output. There is no terminal emulator, no
escape-sequence renderer, and nothing streams process output to the page.

## Known limitations

- **One task at a time.** The second is refused, not queued.
- **No auto-resume.** A task interrupted by a restart is finished; start a new
  one. Its output is kept.
- **No approvals.** A refused tool waits for a person; Cofferdam cannot grant it.
- **No shell, no Bash, no network tools.** Read, Write, Edit, Glob, Grep, inside
  one project folder.
- **No secure input.** Authentication is resolved at the workstation. There is no
  field for a password, and there will not be one until the next milestone builds
  a dedicated ephemeral channel.
- **Bounded output.** 8 MB per task, 256 KB per frame, 16 000 characters of final
  result. Beyond that the task says so rather than silently truncating.
- **Git evidence only.** Cofferdam observes Git and its own process. It does not
  claim complete visibility of everything Claude touched.

## Troubleshooting

**"Claude Code is not installed on this workstation"** — `find_executable` looked
in `~/.local/bin`, `/usr/local/bin`, `/usr/bin`, then `PATH`. Check
`command -v claude`.

**"Claude Code is no longer installed where Cofferdam found it"** — the path was
valid at start-up and is not now, usually an interrupted update. Restart the
service.

**The task waits for sign-in** — run `claude auth status` at the workstation. Do
not type anything into the task.

**"another Claude Code task is already running"** — expected. Wait or cancel.

**"Claude Code started but never reported a ready session"** — the process
launched and produced no `system/init` within 90 s. Usually a CLI that cannot
reach the network.

**The task failed with `claude_no_result`** — the process exited without a result
frame. Exit code zero does not rescue this, deliberately.

**Claude says it cannot run a command** — correct. There is no Bash tool. Ask for
a file change instead, and let Cofferdam's Git probes report what changed.

## Cross-references

- [`AGENT_TASK_CORE.md`](AGENT_TASK_CORE.md) — the provider-neutral foundation:
  states, transitions, events, projects, idempotency, restart semantics.
- [`../DECISIONS.md`](../DECISIONS.md) — the decision record.
