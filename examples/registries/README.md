# Example registries

Committed **placeholders**. Copy them to `$COFFERDAM_HOME/config/registries/` and edit:

```sh
mkdir -p ~/cofferdam/config/registries
cp examples/registries/*.json ~/cofferdam/config/registries/
```

The real files are machine-specific and **never** belong in Git. `.gitignore` keeps
`config/registries/` out of the repository even if `COFFERDAM_HOME` is ever pointed inside a
checkout during development.

These examples contain no real hostnames, account names, serial numbers, tokens, conversation
identifiers, private filesystem paths, or secrets — and no schema here has a field that could
hold one. See [`docs/DEVICE_REGISTRY.md`](../../docs/DEVICE_REGISTRY.md) and
[`docs/APPLICATION_PROFILES.md`](../../docs/APPLICATION_PROFILES.md) for the field-by-field
rules, and [`docs/AGENT_ROUTING.md`](../../docs/AGENT_ROUTING.md) for what the agent and route
registries do and do not do in M2A.

Two things these examples deliberately show:

- `agent_profiles.json` entries are `execution_status: "not-implemented"`. That is the only
  value the schema accepts in M2A — **no agent execution exists**.
- `conversation_routes.json` entries are route *templates*. M2A does not route anything, and no
  live tab id, conversation id, URL, or message text may ever be stored here.

`devices.json` in this directory is the only place a `raspberry-pi` entry appears. It is
descriptive only: M2A implements no Raspberry Pi control, no Wake-on-LAN, and no power actions,
and the schema carries no address, credential, or command field to do it with.
