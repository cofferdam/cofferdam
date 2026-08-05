# Example registries

**These are illustrations of the file format. They are not a description of any real machine, and
they are not a starter configuration.** Every id and name here begins with `example`, on purpose.

## Do not bulk-copy these

There is no `cp examples/registries/*.json ~/cofferdam/config/registries/` step, and nothing in
Cofferdam ever copies these files into `$COFFERDAM_HOME`. Doing it by hand would fill your
machine's configuration — and the PWA — with sample devices and displays that do not exist,
which is exactly the confusion these files were rewritten to avoid.

Start empty. A machine with no registry files is a **valid, fully working** machine: `open_url`
uses the host's usual browser, and the PWA shows honest empty states. Add one file only when you
actually want the thing it configures, and write real entries rather than editing the examples in
place.

## What these files are, and are not

Cofferdam's world has three layers. Registries are two of them, and neither is the third.

| layer | what it is | where it lives |
| --- | --- | --- |
| **Definitions** | code-owned allowlists: which applications exist as a concept (`opera`, `firefox`), which launch adapters and executable candidates are permitted | in the source, not configurable |
| **Runtime resources** | what is *actually* here right now: connected displays, running processes, application instances, windows — later browser tabs and agent task instances | **not implemented yet** — the runtime inventory milestone (M2B) |
| **User overlays** | optional names, aliases, preferences, policy metadata | these registry files |

So:

- `applications.json` names **definitions** the code already supports. It does not tell you what
  is installed, and it cannot introduce a new application.
- `displays.json` holds **optional labels**. It is not a list of connected displays, and writing
  an entry does not make a display exist. Display discovery is M2B; a discovered display may
  *then* be given a label such as "Büyük monitör".
- `browser_profiles.json` holds **optional launch preferences** — which browser opens a URL and
  which domains it may open. An entry is not an open browser window and not a running process.
- `agent_profiles.json` entries are placeholders. `execution_status` can only be
  `"not-implemented"`, which is the whole truth: **no agent execution exists**.
- `conversation_routes.json` entries are route *templates*. M2A routes nothing, and no live tab
  id, conversation id, URL, or message text may ever be stored here.

## What is never in these files

No real hostnames, account names, serial numbers, tokens, conversation identifiers, private
filesystem paths, or secrets — and no schema here has a field that could hold one. No executable
paths, command strings, argv, shell fragments, desktop-file paths, or environment overrides
either: those stay code-owned.

## Where the field-by-field rules live

- [`docs/CONTROL_PLANE.md`](../../docs/CONTROL_PLANE.md) — the three layers, and the identity
  rules the runtime inventory milestone must follow
- [`docs/DEVICE_REGISTRY.md`](../../docs/DEVICE_REGISTRY.md) — devices and displays
- [`docs/APPLICATION_PROFILES.md`](../../docs/APPLICATION_PROFILES.md) — applications and browser
  profiles
- [`docs/AGENT_ROUTING.md`](../../docs/AGENT_ROUTING.md) — agent profiles and route templates
