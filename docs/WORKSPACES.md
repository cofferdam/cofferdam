# Workspaces and Working Context

M2J PR1. What Cofferdam knows about **what you are working on**, how it is configured, and
what it deliberately does not know yet.

Decided in [`DECISIONS.md`](../DECISIONS.md) D-2026-08-11-1 and D-2026-08-11-3; scoped in
[`ROADMAP.md`](../ROADMAP.md) under M2J.

## The two halves

A **workspace** is host-owned configuration: a stable id, a label, and the id of a project that
already exists. It lives in `$COFFERDAM_HOME/config/workspaces.json`, beside `task-projects.json`
and the M2A registries, and it is edited in a text editor on the workstation. **There is no route
that writes it**, for the same reason there is no route that writes a project: a registry that can
be written over the network is a registry that can grant access over the network.

**Working Context** is Cofferdam-owned durable state: the active workspace, each workspace's
objective and its history, and a small set of bounded continuity references. It lives in SQLite at
`$COFFERDAM_HOME/state/workspace/workspace.sqlite3` — its own database, not `tasks.sqlite3`.

The split matters. One is a thing a person writes down; the other is a thing the product records.
Working Context is **state, not memory**, and D-2026-08-11-3 is explicit that it must never become
a second Markdown authority.

## Configuring a workspace

```json
{
  "workspaces": [
    {
      "workspace_id": "cofferdam",
      "display_name": "Cofferdam",
      "project_id": "cofferdam",
      "enabled": true,
      "notes": "the main repository"
    }
  ]
}
```

Five fields, and that is the whole schema. A copy-and-edit starting point is in
[`examples/workspaces.json`](../examples/workspaces.json).

| Field | Meaning |
|---|---|
| `workspace_id` | stable identity; lowercase, digits, dash, underscore, ≤ 64 chars |
| `display_name` | what a person sees; defaults to the id |
| `project_id` | a project in `task-projects.json` — the binding that gives the workspace a directory and a worker |
| `enabled` | `true` unless written otherwise; must be a real boolean |
| `notes` | one short line, optional |

A missing file is not an error and is the shipped default: a workstation with no workspaces has
none, and every existing task, Custom GPT and Claude flow works exactly as before.

### What a workspace may never carry

Refused **by name**, with a message saying where the decision actually lives:

- `root`, `path`, `directory` — a workspace reaches a directory only through its project, where
  root validation, the symlink walk and the re-check before every task already live.
- `adapters`, `adapter`, `delegated_adapter`, `model`, `models`, `provider` — **the important
  one.** PR #34 made "which agent runs here" an explicit decision on the *project*, after finding
  that list ordering had quietly been the authority. A workspace that could name an adapter would
  recreate that one level up, and worse: the workspace is the thing a client switches.
- `argv`, `cmd`, `command`, `env`, `environment`, `exec`, `executable`, `prompt`, `script`,
  `shell` — configuration says where and what, never what to run.
- `secret`, `secrets`, `token` — configuration is not a credential store.

An *unrecognised* field is different and is kept: it may be configuration written for a later
version, and dropping the workspace would break a host that upgrades and rolls back. A forbidden
field is an attempt to move a decision, and that is loud.

### Why there are no document roles or profiles yet

The recorded M2J scope includes document-role mappings for the mind (`plan_doc`, `status_doc`,
`decisions_doc`) and Auto/Safe/Review evaluation profiles. Neither is here, deliberately.

A `plan_doc` would name a file nothing reads until PR3. A `profile` would name an evaluation depth
that has no evaluator until M2K. Each would validate, persist, appear in an API response and mean
nothing — and a field that means nothing is one somebody builds on before it means something. The
schema is additive; a later PR widens it without migrating anything.

## What Working Context holds

| Field | Kind | Notes |
|---|---|---|
| active workspace | persisted | one pointer, at most one row |
| `objective` | persisted | ≤ 500 chars, per workspace, with history |
| `expected_next_step` | persisted | ≤ 500 chars — a note for a person |
| `plan_checkpoint` | persisted | ≤ 200 chars, **opaque in this milestone** |
| `pending_decision_ref` | persisted | ≤ 200 chars, opaque |
| `latest_evidence_ref` | persisted | ≤ 200 chars, opaque |
| `active_task_id` | persisted | a *reference*; must be a real task in this workspace's project |
| task state, bucket, terminality | **derived** | asked of Task Core on every read |
| `delegated_worker`, `delegation` | **derived** | resolved through the project |
| display names, `project_available` | **derived** | resolved through config |

**Context is keyed by workspace, not stored once.** The obvious shape — one objective, one next
step — is wrong in a specific way: switch to another workspace and the objective *stays*, now
describing something you are not doing; switch back and the original is gone. Per-workspace rows
make switching a pointer move that disturbs nothing, and make cross-workspace confusion
structurally impossible rather than a rule to remember.

**An objective is refused rather than truncated** when it is too long. Adapter-reported text is
truncated because trimming it loses nothing a person chose; an objective is *authored*, and
silently storing half of it would show somebody a sentence they did not write.

### The three opaque references

`plan_checkpoint`, `pending_decision_ref` and `latest_evidence_ref` are bounded strings that
nothing resolves yet. That is stated in the payload's own `limitations` list rather than only
here. They exist so that PR3 can later answer "which part of the plan is active?" and M2K can
point at a bundle — and they are strings rather than rich objects precisely because inventing a
structure for a system that does not exist is how the structure turns out wrong.

Absence is stored as absence. Clearing a field writes `NULL`, never `""`, so "never set" and "set
to nothing" cannot render identically.

## Authority

**Task Core owns tasks.** Working Context points at one; Task Core answers everything about it. The
state, bucket and terminality in a workspace read come from a live lookup, never from a stored
copy — a cached `task_state` would be correct for a few seconds and then wrong with nothing
announcing it, which is the failure Task Core's own restart reconciliation exists to prevent.

A task reference resolves to one of three words:

- `live` — the task exists and is not terminal;
- `terminal` — it finished, failed, was cancelled or was interrupted. **The reference is kept**: it
  finished, which is the fact somebody came back to read, and clearing it on completion would
  delete the answer at the moment it became interesting;
- `missing` — Task Core does not have it. The id is still shown, because the reference is what
  somebody chose and blanking it would hide that a task was deleted.

Pointing at a task in a *different* project is refused. A workspace tracking another workspace's
task would render perfectly well and be wrong.

**The project owns the worker.** `delegated_worker` is read through the workspace's project to
`TaskProject.delegation()` on every request and is never stored. A persisted worker would be a
second authority holding a stale copy — strictly worse than the ordering bug PR #34 fixed.

**Nothing here executes anything.** Setting `expected_next_step` to "Run the validation pass"
records a sentence. Cofferdam does not run it, schedule it, or offer to. D-2026-08-11-11 keeps the
loop human-directed, and there is a test asserting that storing that sentence creates no task.

## The API

All six routes are on the private device-token surface, over the tailnet.

| Route | Does |
|---|---|
| `GET /api/workspaces` | configured workspaces, names and ids only, with whether each project is usable |
| `GET /api/workspace/current` | what are we working on right now |
| `PUT /api/workspace/active` | switch the active workspace, or `null` to deactivate |
| `PUT /api/workspace/objective` | set or clear the objective; the previous one goes to history |
| `PUT /api/workspace/context` | partial update of the bounded continuity fields |
| `GET /api/workspace/objective-history` | previous objectives, newest first, bounded |

**The Actions bridge reaches none of them.** These routes use `require_token`, which has never
heard of the bridge credential, so a bridge request is a 401 because nothing here can recognise it
— not because a check refuses it. `syncWorkspace` is an M2J **PR4** decision; until it is designed
and reviewed, no external surface reads the workspace at all. A test presents a real bridge
credential to every route and to a task route as a control.

`GET /api/workspace/current` **never errors for an ordinary state.** No workspace configured, none
active, the active one renamed out of the file, its project disabled — each is a `problem` word on
a `200`, because every one is a real state of a working host and a client has to render it:

| `problem` | Means |
|---|---|
| `no_active_workspace` | nothing has been activated |
| `active_workspace_unconfigured` | the stored id is no longer in the file; the id is shown so it can be restored |
| `active_workspace_disabled` | configured, and switched off since activation |
| `active_workspace_project_missing` | the project was removed, renamed or disabled |

Bodies are closed vocabularies: an unknown key is a `422`, not a silent drop. That distinction is
load-bearing for `PUT /api/workspace/context`, which is a *partial* update — `null` clears, absence
leaves alone — so a typo that silently did nothing would look exactly like a value that was
accepted. `source` is not a client field: provenance is assigned from the authenticated surface,
for the reason task `origin` is.

Every content response carries `Cache-Control: no-store`. The body holds somebody's objective and
expected next step — their own words about their own work, which is the same class of content as a
task result.

## Storage

Its own database, deliberately. `tasks.sqlite3` has one job and a strong invariant — a state change
and its event land in one transaction — and putting rows with a different lifetime under that lock
would make "can this file be deleted?" a question with two answers.

Here it has one: **delete `state/workspace/` and the host forgets which workspace was active**,
while every task, turn, clarification and result is untouched. That is the rollback story, and it
is only true because the files are separate.

Posture copied from Task Core rather than re-decided: WAL, `synchronous=FULL`, `foreign_keys=ON`,
`0700` on the directory and `0600` on the database and its WAL/shm siblings. Schema version 1;
newer databases are refused rather than downgraded.

**A read never creates the database.** The PWA polls, and an ordinary connect would manufacture a
state directory out of somebody looking at a screen. On a host that has never activated a
workspace, every read answers from nothing — no active workspace, an empty context, no history —
and no file appears.

## What is not in this milestone

No mind, vault, `USER.md`, project-memory reading or memory proposals (PR2). No Context Builder,
`LocalContextPack` or `CloudContextProjection` (PR3). No PWA panel and no `syncWorkspace` (PR4). No
evidence bundles or evaluation (M2K). No planner, model runtime or Ollama (M2L). No dashboard
(M2M). No workspace creation over the API, and no automatic workspace for an existing project —
D-2026-08-11-1 says suggested and confirmed, never silently auto-created, and PR1's honest form of
that is to suggest nothing yet.
