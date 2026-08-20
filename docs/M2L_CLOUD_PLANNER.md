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

## Still deferred

User confirmation/answer API · worker dispatch · worker result evaluation · next-step planner pass ·
Project Handoff · Custom GPT integration · phone/PWA UI · bounded autonomous loop · routines ·
artifacts · MCP server · connectors · deployment.

The first product mode stays **prepare a prompt, then wait for the user**. There is no autonomous
continuation anywhere in this milestone.
