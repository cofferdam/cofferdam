# Gate A — exposing the Actions bridge (M2I.5 PR2)

[`ACTIONS_BRIDGE.md`](ACTIONS_BRIDGE.md) describes what the bridge *is*. This
document describes the one thing PR1 deliberately did not do: give it a public
origin, and connect a real private Custom GPT to it.

Gate A is the first time Cofferdam has been reachable from the internet at all.
Everything below exists to keep that reachability down to eight bounded
operations on one hostname.

---

## The deployed shape

```
  Private Custom GPT  (ChatGPT, visibility: only me)
          │
          │  HTTPS GPT Actions, Bearer, TLS 1.2+ on 443
          ▼
  actions.efeaydinalp.com                    ← Cloudflare edge, real certificate
          │
          │  Cloudflare Tunnel, ONE ingress rule + a 404 catch-all
          ▼
  127.0.0.1:7210                             ← cofferdam-actions-bridge.service
          │
          │  fixed internal client, ten named methods, no path parameter
          ▼
  <tailnet address>:7101                     ← cofferdam-workstation.service
```

The arrow from Cloudflare stops at loopback. It does not fan out.

### What stays private, and why it cannot be otherwise

The PWA, the main Cofferdam API, the production root path, Remote Control, the
registry endpoints, the task event streams, the provider surface, every generic
`/api` path, the filesystem and local service management are **not in the
ingress file**. Cloudflare cannot reach a service the ingress does not name, so
this is an absence rather than a denial — there is no rule to relax, no
allowlist to grow, and no configuration edit that widens it by one line.

The workstation daemon additionally binds only its Tailscale address. Even a
tunnel misconfigured to point at 7101 would need that address written into it;
`tests/test_actions_exposure_deploy.py` fails if `7101` appears in the ingress
template at all.

## Two keys, and they are never the same bytes

| | Held by | File | Blast radius |
|---|---|---|---|
| **External Actions key** | the Custom GPT, stored on OpenAI's side | `$COFFERDAM_HOME/secrets/actions-bridge-key` | the eight Actions |
| **Internal bridge token** | the bridge process | `$COFFERDAM_HOME/secrets/actions-bridge-internal-token` | ten task routes |
| Device token (unchanged) | the phone / PWA | `$COFFERDAM_HOME/secrets/token` | the whole private API |

All three are mode `0600` and the bridge refuses to start if either of its two
is readable by anybody else — refuses, rather than correcting the mode, because
a secret that was briefly readable may already have been read.

Neither bridge credential has an environment variable. That is not an oversight:
an env var is visible in `/proc` and inherited by every child, and both
processes that need the internal token are on the same host reading the same
file.

**The external key is never printed by anything.** Not by the generator, not by
the verification script, not by the units. To copy it into the GPT editor, read
the file yourself:

```bash
cat ~/cofferdam/secrets/actions-bridge-key
```

Do that in your own terminal. Do not paste the value into a ChatGPT
conversation, a Claude conversation, an issue, or a commit — it belongs in
exactly one box, the GPT editor's authentication panel.

---

## Install

Everything here is user-owned. Nothing needs root.

### 1. Enable the scoped internal caller on the daemon

```bash
install -m 0644 deploy/dropins/20-actions-bridge-caller.conf \
  ~/.config/systemd/user/cofferdam-workstation.service.d/20-actions-bridge-caller.conf
systemctl --user daemon-reload
systemctl --user restart cofferdam-workstation.service
```

The drop-in carries one boolean and nothing else. On restart the daemon
generates `secrets/actions-bridge-internal-token` (0600) and begins accepting it
on ten task routes — recording `origin = chatgpt_app` for work that arrives that
way, so a bridge-created task is never mislabelled as somebody's phone.

Every other route keeps the unchanged `require_token`, which has never heard of
this credential.

### 2. Generate the external Actions key

```bash
cd ~/cofferdam/slots/<active>
COFFERDAM_HOME=~/cofferdam ./.venv/bin/python -m cofferdam.actions_bridge --generate-key
```

The value is not printed. To rotate later, add `--force` — and expect to
re-enter the new value in the GPT editor, because the Custom GPT holds the old
one:

```bash
COFFERDAM_HOME=~/cofferdam ./.venv/bin/python -m cofferdam.actions_bridge --generate-key --force
systemctl --user restart cofferdam-actions-bridge.service
```

The old key keeps working until that restart, deliberately: the running process
closed over the value at startup, so a half-written file cannot lock the bridge
out mid-flight.

### 3. Configure and start the bridge

```bash
cp deploy/actions-bridge.env.example ~/cofferdam/actions-bridge.env
chmod 600 ~/cofferdam/actions-bridge.env
# edit: COFFERDAM_HOME, and COFFERDAM_BRIDGE_INTERNAL_BASE_URL if the daemon
# does not listen on loopback (it binds its Tailscale address on this host)
```

Check configuration and both credentials without binding anything:

```bash
cd ~/cofferdam/slots/<active>
COFFERDAM_HOME=~/cofferdam ./.venv/bin/python -m cofferdam.actions_bridge --check
```

```bash
install -m 0644 deploy/cofferdam-actions-bridge.service ~/.config/systemd/user/
# on a host whose active slot is B:
install -D -m 0644 deploy/dropins/10-actions-bridge-slot.conf.example \
  ~/.config/systemd/user/cofferdam-actions-bridge.service.d/10-actions-bridge-slot.conf
systemctl --user daemon-reload
systemctl --user enable --now cofferdam-actions-bridge.service
```

Verify locally before anything is exposed:

```bash
deploy/verify-actions-exposure.sh
```

### 4. Create the tunnel

`cloudflared` must come from an official Cloudflare distribution — the apt
repository, the GitHub release published by Cloudflare, or the vendor package.
Never a mirror.

The login step opens a browser and is the one part of this that cannot be
scripted:

```bash
cloudflared tunnel login
```

It writes an **account certificate** to `~/.cloudflared/cert.pem`, which
authorizes managing tunnels for the whole account. Treat it as a credential.

```bash
cloudflared tunnel create cofferdam-actions
```

That writes a per-tunnel credentials JSON. Move it beside Cofferdam's other
secrets and lock it down:

```bash
mkdir -p ~/cofferdam/secrets/cloudflared && chmod 700 ~/cofferdam/secrets/cloudflared
mv ~/.cloudflared/<uuid>.json ~/cofferdam/secrets/cloudflared/
chmod 600 ~/cofferdam/secrets/cloudflared/<uuid>.json
```

```bash
cp deploy/actions-tunnel.yml.example ~/cofferdam/config/actions-tunnel.yml
chmod 600 ~/cofferdam/config/actions-tunnel.yml
# edit: tunnel name, credentials-file path, and the hostname
```

Validate the ingress **before** any DNS record exists:

```bash
cloudflared --config ~/cofferdam/config/actions-tunnel.yml tunnel ingress validate
cloudflared --config ~/cofferdam/config/actions-tunnel.yml tunnel ingress rule https://actions.efeaydinalp.com/v1/health
```

Then route DNS and start the connector:

```bash
cloudflared tunnel route dns cofferdam-actions actions.efeaydinalp.com
install -m 0644 deploy/cofferdam-actions-tunnel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cofferdam-actions-tunnel.service
```

```bash
deploy/verify-actions-exposure.sh --host actions.efeaydinalp.com
```

### 5. Render the production schema

`docs/custom-gpt/openapi.yaml` stays a placeholder on purpose. The document you
paste into the GPT editor is rendered onto the host that owns the origin:

```bash
python3 deploy/render-actions-openapi.py --hostname actions.efeaydinalp.com
```

It substitutes the server URL and its description and copies every other byte
through, then verifies the result: one server, every `operationId` intact, the
consequential markings unchanged, and no loopback address, tailnet address or
filesystem path anywhere in it.

---

## Cloudflare Access is deliberately off

Cloudflare Access puts a browser-based identity challenge in front of a
hostname. A GPT Action is a **server-to-server** call from OpenAI's
infrastructure with one `Authorization` header and no browser, no cookie jar and
no interactive step — and OpenAI's production notes state that custom headers
are not supported, so an Access service-token header cannot be added either.

Enabling Access in front of this hostname would break every Action while adding
nothing: the bridge's own Bearer key is already the application authentication
boundary, checked before any request reaches Cofferdam.

The same reasoning rules out any WAF rule or Bot Fight setting that would
challenge a non-browser client.

## Boot and persistence

Both units are `WantedBy=default.target` with `loginctl enable-linger` already
set for this user, which is what makes the workstation daemon survive a reboot
with nobody logged in. Neither new unit names `graphical-session.target` in any
direction — that combination is the M1.1 login-loop regression, and
`tests/test_actions_exposure_deploy.py` fails if it reappears.

The bridge is ordered `After=` the daemon but does not `Require=` it: a bridge
whose daemon is briefly down should still answer `/v1/health`, because an
operator staring at a dead tunnel needs to be able to tell a transport problem
from an upstream one. The tunnel is ordered after the bridge and retries its
origin indefinitely, so a bridge restart is a few 502s rather than a reconnect
storm.

## Known limitations

These are properties of the products involved, not defects to work around.

- **No background push.** Cofferdam cannot put a message into a ChatGPT
  conversation. The user or the GPT must call `syncTask` during a turn. The
  operator instructions forbid promising otherwise.
- **No artifacts.** Cofferdam has no task-owned artifact model, so `syncTask`
  reports `artifacts_supported: false` with a reason rather than an empty list.
- **One question shape.** `single_choice` with Cofferdam-minted option ids.
  Free text, multiple choice and unknown modes come back as
  `clarification_supported: false` with the real question text intact.
- **No "Other" with custom text.** An option labelled "Other" is submittable as
  a plain choice; there is no way to attach text to it, and nothing is
  approximated in its place.
- **No tool approvals.** A clarification asks for information; an approval asks
  for permission to act. The private API has no approval route, so there is
  nothing for the bridge to expose. A waiting task reports
  `local_action_required` and the GPT cannot satisfy it.
- **Consequential actions always confirm.** Every write carries
  `x-openai-isConsequential: true`, which suppresses ChatGPT's "always allow"
  button. That is a cost paid on purpose.
- **Reduced tunnel HA on a network that filters outbound 7844.** cloudflared
  needs TCP 7844 to Cloudflare's edge for both QUIC and HTTP/2, with no fallback
  to 443. On a network where only part of the edge is reachable the connector
  establishes fewer than four connections. That is degraded redundancy, not a
  broken tunnel, and it is reported rather than hidden.
- **Mobile clients are unverified for this deployment.** Gate A validates the
  web GPT editor's Preview. There are current third-party reports of Actions not
  being invoked on some mobile ChatGPT builds; nothing here claims otherwise.
- **A build from before this PR logs canonical task ids.** `internal.py` uses
  httpx, and httpx logs one INFO line per request carrying the full upstream
  URL — which for `syncTask` ends in `/api/tasks/task_<26 chars>`. That is
  exactly the identifier `observe.py` keeps out of its own line, and having it
  two rows above makes the join a leaked journal would otherwise not permit.
  `__main__.py` now sets the httpx and httpcore loggers to WARNING at startup,
  beside the line that disables uvicorn's access log for the same reason.

  `LogFilterPatterns=` in the unit was tried first and **measured**: on systemd
  259 it dropped neither the service's stdout nor a native `systemd-cat`
  message, so it is deliberately absent rather than present and useless. A host
  running the bridge from a commit before this fix keeps writing those lines
  into its own local journal until it is rebuilt; nothing about it is externally
  reachable.

## The provider-usage gate

Listing projects, listing recent tasks and syncing a task that does not exist
call no model and cost nothing. **Creating a task runs an agent.** That is a
separate decision from exposing the bridge, and it requires its own explicit
approval — which project, which adapter, what prompt, what it may cost.

Gate A is complete without ever creating one.

## Gate B is still separate

Production runs the **Claude Code adapter** and nothing else. The Claude Agent
SDK adapter is not installed in the production environment, is not enabled, and
is not selected by any project in the registry.

Structured clarifications (`AskUserQuestion` round trips) need that adapter, so
Gate A does not demonstrate them and this document does not claim it does.
Enabling the Agent SDK in production is **Gate B**, a separate approval, and it
may be granted or refused independently of this one.

---

## Rollback

Non-destructive, in this order. Task history and the project registry are never
touched.

1. **In the GPT editor**, remove the Action (or clear the schema) so ChatGPT
   stops calling the origin. Do this first — the rest makes the origin fail,
   and a GPT retrying a dead endpoint is noise in the journal.
2. **Stop the tunnel.**
   ```bash
   systemctl --user disable --now cofferdam-actions-tunnel.service
   ```
3. **Remove the public hostname.**
   ```bash
   cloudflared tunnel delete cofferdam-actions
   ```
   The DNS record is independent of the tunnel and is **not** removed by that
   command — delete the `actions` CNAME in the Cloudflare dashboard as well, or
   visitors get a 1016 error against a record that still exists.
4. **Stop the bridge.**
   ```bash
   systemctl --user disable --now cofferdam-actions-bridge.service
   ```
5. **Destroy the external key.**
   ```bash
   shred -u ~/cofferdam/secrets/actions-bridge-key
   ```
6. **Revoke the internal credential.**
   ```bash
   shred -u ~/cofferdam/secrets/actions-bridge-internal-token
   ```
7. **Remove the caller drop-in and restart the daemon.**
   ```bash
   rm ~/.config/systemd/user/cofferdam-workstation.service.d/20-actions-bridge-caller.conf
   systemctl --user daemon-reload
   systemctl --user restart cofferdam-workstation.service
   ```
   After this the daemon knows exactly one credential again.
8. **Only if the merged-main deployment itself is faulty**, roll the runtime
   slot back by restoring the previous `ExecStart`/`WorkingDirectory` drop-in
   from `~/cofferdam/state/service-backups/` and restarting. The previous slot
   is never deleted.
9. **Verify what survived.** The PWA reaches Cofferdam over Tailscale, the
   Claude Code adapter still runs tasks, and every past task is still there.

Steps 2–7 are independent of step 8: the external surface can be removed
completely while leaving the merged-main deployment in place.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `1016 Origin DNS error` | the tunnel is down or the DNS record points at a deleted tunnel — `systemctl --user status cofferdam-actions-tunnel` |
| `502` from the public origin | the tunnel is up and the bridge is down — `systemctl --user status cofferdam-actions-bridge` |
| `401` on every Action | the key in the GPT editor is not the one in `secrets/actions-bridge-key`, or the bridge was not restarted after a rotation |
| bridge refuses to start | run `--check`; a credential file that is missing, empty, a symlink, or readable by more than its owner is a startup failure by design |
| `502` on authenticated operations, `200` on health | the bridge is up and cannot reach the daemon — check `COFFERDAM_BRIDGE_INTERNAL_BASE_URL` against the daemon's actual bind address |
| fewer than four tunnel connections | outbound TCP 7844 is partly filtered on this network; try `protocol: http2` and `edge-ip-version: 4` |
| ChatGPT asks to confirm every read | a GET was marked consequential somewhere; compare the rendered schema against `docs/custom-gpt/openapi.yaml` |

Everything in the bridge journal is bounded metadata — a request id, the
operationId, the display reference, a status, a duration. There is no log line
anywhere that could carry a credential, a header, a body, a prompt or a result,
because `log_request` has no parameter that could hold one.
