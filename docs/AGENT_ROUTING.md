# Agent profiles and conversation routing

Files:

```
$COFFERDAM_HOME/config/registries/agent_profiles.json
$COFFERDAM_HOME/config/registries/conversation_routes.json
```

Common envelope, ID and alias rules are in [`DEVICE_REGISTRY.md`](DEVICE_REGISTRY.md).

> **Nothing in this document is implemented in M2A.** No agent runs, no Claude Code session
> starts, no message is sent, no conversation is routed, and no browser DOM is touched. These two
> registries exist so the *shape* of that work is fixed before any of it is built — and so the
> constraints below are written down while they are still cheap to keep.
>
> These registries are **user overlays**, not a live inventory. An agent profile is a name for an
> adapter that does not exist yet; it is not a running session. Enumerating live agent task
> instances is a later milestone, after *M2B — Runtime inventory*.

---

## The eventual shape

The intended capability is: a conversation started somewhere — a ChatGPT tab in Opera, a note, a
phone message — is handed to an agent session on this machine, the agent does work, and the result
comes **back to the conversation it came from**.

That requires a durable record per routed task. Every future routed task will have:

| field | why |
| --- | --- |
| `task_id` / `correlation_id` | the one thing that ties an origin, an agent, and a return together |
| origin adapter | which surface it arrived from (extension, PWA, connector) |
| origin conversation key | *which* conversation there, opaquely |
| target agent session | the live session doing the work |
| return route | where a result goes when the work finishes |
| status | running / waiting / done / failed |
| result | what to hand back |

**Those records are not implemented in this PR**, and none of those fields may be smuggled into
the registries below. Registries are static configuration; the record above is runtime task state.
Mixing them is how a configuration file quietly becomes a place where someone's private
conversation identifiers accumulate and then get backed up, logged, and read by whatever comes
next.

---

## `agent_profiles.json`

Metadata placeholders. That is the entire M2A scope.

```json
{
  "id": "example-claude-code",
  "name": "Example — Claude Code adapter",
  "aliases": [],
  "enabled": true,
  "adapter_kind": "claude-code",
  "execution_status": "not-implemented"
}
```

| field | rule |
| --- | --- |
| `adapter_kind` | `claude-code`, `codex-cli`, `ollama`, `custom-placeholder` |
| `execution_status` | must be `not-implemented` |

`execution_status` is a closed vocabulary with exactly one member in M2A, so a registry **cannot
promote itself** to "ready". Changing that value requires changing the code that implements
execution, in the milestone that implements it.

M2A stores none of: executable paths · project filesystem paths · prompts · credentials · model
API keys · shell or CLI arguments · session state. No field exists for any of them, and each of
those names is on the code-owned denylist.

The PWA may display these profiles. It displays them with an explicit **"not implemented"** badge,
and offers no Start, Send, Run, or Route control. A card that merely looked inert would still
imply the feature exists.

---

## `conversation_routes.json`

**Static route templates, not live conversation records.**

```json
{
  "id": "example-connector-route",
  "name": "Example — connector hand-off, prepared reply needs confirming",
  "aliases": [],
  "enabled": true,
  "source_kind": "future-connector",
  "target_agent_profile_id": "example-claude-code",
  "return_mode": "prepare-then-confirm"
}
```

| field | rule |
| --- | --- |
| `source_kind` | `manual`, `opera-extension`, `future-connector` |
| `target_agent_profile_id` | must reference an agent profile |
| `return_mode` | `manual-copy`, `prepare-then-confirm` |

M2A does not implement the route. A template says "if this kind of thing were routed, it would go
there, and results would come back that way". Nothing acts on it.

Never stored here: browser tab IDs · ChatGPT conversation IDs · DOM selectors · URLs identifying
private conversations · message text · prompts · agent outputs · access tokens. Again — no field,
denylist, unknown fields rejected.

The PWA shows these with an explicit **"template only"** badge and no route control.

### `return_mode`

- `manual-copy` — a human moves the result. No automation, no send.
- `prepare-then-confirm` — the system may *prepare* a reply and show it, but a human confirms
  before anything leaves. This is the default posture for anything that speaks on the user's
  behalf.

Sending a message is one of the actions that requires policy-driven confirmation, alongside
merging code, shutdown, reboot, destructive actions, and physical power control. M2A implements
none of them, and records the rule so the first one cannot arrive without a confirmation path.

---

## How an existing ChatGPT conversation will actually connect

Recorded as future architecture so the runtime inventory milestone does not adopt a wrong
assumption. **None of it is implemented in this PR.**

Two mechanisms, usable separately or together:

1. **A ChatGPT App / MCP tool** for explicit task dispatch and result retrieval. The conversation
   calls a tool; the tool talks to the daemon over a defined protocol; results come back through
   the same channel. This is the clean path, because both ends are official interfaces.
2. **A permission-bounded Opera companion extension** that associates a browser tab and its
   conversation with a task, and prepares the returned result *in that same conversation* for a
   human to confirm. The extension supplies the browser's own tab identity — it never has to
   guess, and Cofferdam never has to infer a tab from a process.

Neither mechanism gives Cofferdam the right to speak on the user's behalf: `prepare-then-confirm`
still means a human confirms before anything leaves.

### Cursor

- **Cursor is not a way to access or continue an existing ChatGPT consumer conversation.** It is
  not a client for those conversations and must never be treated as one. Any design that routes a
  ChatGPT conversation "through Cursor" is built on a false premise.
- **Cursor CLI is a future *target-agent* adapter**, in the same category as Claude Code: something
  a task can be dispatched *to*. That is the only role it has here.
- It is not implemented in this PR, and `adapter_kind` deliberately gains no `cursor-cli` member
  yet — adding one would advertise a capability that does not exist.

## Boundaries this design keeps

- **Browser DOM automation is a replaceable adapter, never the core architecture.** If it ships,
  it sits behind a Cofferdam-owned interface and can be removed without redesigning routing.
- **An Opera extension, if it ever exists, is permission-bounded input.** It may hand the daemon a
  request. It never becomes the place decisions are made, and it is not built in M2A.
- **The Python daemon owns routing records.** Not the browser, not the extension, not a desktop
  companion.
- **Tab identity comes from the browser's own extension API.** It must never be inferred from
  Chromium process IDs: a tab is not a process, and that mapping is neither stable nor observable.
- **No pixel-coordinate or mouse-position automation**, here or anywhere. Prefer official APIs,
  CLI protocols, MCP, browser extension APIs, D-Bus, systemd, desktop portals, and semantic
  accessibility interfaces. Where no semantic interface exists, the honest answer is that the
  capability is unavailable — not to aim at a pixel.
