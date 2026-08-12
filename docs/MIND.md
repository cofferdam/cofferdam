# Cofferdam Mind

M2J PR2. What Cofferdam may **read** of your memory, what it may be allowed to **change**, and
who decides each.

Decided in [`DECISIONS.md`](../DECISIONS.md) D-2026-08-08-6, D-2026-08-11-3 and D-2026-08-11-4;
scoped in [`ROADMAP.md`](../ROADMAP.md) under M2J. Builds directly on the workspace model in
[`docs/WORKSPACES.md`](WORKSPACES.md).

## Markdown is the memory

Cofferdam's memory is Markdown files a person can read and edit with Cofferdam stopped. The
SQLite database this milestone adds holds **workflow state only** — which changes have been
proposed and not yet decided. Delete it and your memory is untouched. Where it and the Markdown
ever disagree, the Markdown is right.

The vault is **Obsidian-compatible and Obsidian-independent**: plain CommonMark,
`[[wikilinks]]`, optional YAML frontmatter, in an ordinary directory. Cofferdam never launches
Obsidian, never reads `.obsidian/`, and never writes its configuration. Obsidian is not a
runtime dependency and there is no plugin.

## Two minds, two authorities

|  | Global mind | Project mind |
|---|---|---|
| Lives in | a dedicated vault **outside** `$COFFERDAM_HOME` | the project's own repository |
| Granted by | `config/mind-grant.json` | the workspace's `documents` map |
| Default | **absent** | **absent** |
| Holds | user-level, cross-project memory | that project's own memory |
| Roles | `user`, `communication_style`, `preferences` | `project`, `plan`, `research`, `decisions`, `status`, `design` |

The two vocabularies are disjoint on purpose. Asking for `status` in the global scope, or `user`
in the project scope, is not a sensible-looking question with a careful answer — it is a
refusal in a lookup table.

**Project documents are never copied into the vault**, in either direction. For Cofferdam itself
the existing documents are role-mapped — `plan` is `ROADMAP.md` — and **no `PLAN.md` is added**
to satisfy a naming convention (D-2026-08-11-3).

## A request names a role, never a file

This is the whole access model:

> A caller identifies a **semantic role**. The host decides which file that is.

There is no field, path segment, query parameter or body key anywhere in this API for an
absolute path, a relative path, a root, a working directory, a filename, a filesystem URI or a
shell command. `scope` and `role` are matched against closed, code-owned vocabularies — nine
words in total — **before** anything is resolved, so request text never becomes a path
component. A role sent as `../../etc/passwd` is not sanitised; it is simply not a role, and the
refusal happens before any filesystem call.

### Mapping project roles

In `config/workspaces.json`, on the workspace:

```json
{
  "workspace_id": "cofferdam",
  "project_id": "cofferdam",
  "documents": {
    "status": "STATUS.md",
    "plan": "ROADMAP.md",
    "decisions": "DECISIONS.md",
    "design": "DESIGN.md"
  }
}
```

PR1 deliberately left this field out under its own rule — *add a field when the thing that reads
it exists*. PR2 is that thing, so it arrives now, with a consumer.

It is **not a second path authority**. The value is a plain relative name; the directory still
comes from the workspace's project in `task-projects.json`, where root validation, the symlink
walk and the re-verification before every use already live. `root`, `path` and `directory` are
still refused on a workspace by name.

A role mapped twice is **refused**, not resolved by position: the file is parsed with a hook
that rejects duplicate keys, because `json.loads` silently keeps the last one and that would
make file order the authority over which document a role resolves to.

### Granting the vault

In `config/mind-grant.json` — a file that does not exist until you write it:

```json
{
  "global_vault": {
    "root": "/home/you/cofferdam-mind",
    "enabled": true,
    "documents": { "user": "USER.md", "preferences": "PREFERENCES.md" }
  }
}
```

**Without this file there is no global mind**, and that is an absence rather than a refusal:
`MindService` has no root to resolve against. Nothing scans `~`, `~/Documents` or an existing
Obsidian vault; nothing offers a directory it found; Cofferdam never chooses where your vault
lives. There is no route that creates, edits or reads this file — a grant that could be written
over the network would be access that could be granted over the network.

It gets the same treatment as a project root, because it is the product's second filesystem
grant: absolute literal path, no `~`/`$`/`..`, `lstat` over every component, re-verified at use.

### `enabled: true` is the grant — writing the file is not

Deliberately **stricter than the project and workspace convention**, where `enabled` defaults to
`true` and omitting it means on. Those files say where work happens on this machine; this one
decides whether your personal, cross-project memory is readable at all, so the convenient default
is the wrong default (D-2026-08-12-2).

| State | Result |
|---|---|
| file absent | no vault |
| present, `enabled` omitted | no vault, **reported** |
| `enabled: false` | no vault, reported |
| `enabled` not a boolean (`1`, `"true"`, `"yes"`) | no vault, reported |
| `enabled: true` and otherwise valid | **the vault** |

The three written-but-inactive states are reported rather than silent, because each is somebody
having written the file and not got what they expected — and "I never granted one" and "I granted
one and it is off" send you to different lines. The check is an `isinstance` against `bool`, not a
truthiness test: `1` and `"true"` are exactly what a person writes meaning yes, and exactly what
must not be read as consent.

`enabled: true` never rescues an otherwise invalid grant — a relative root, a `~`, a project role
in the vault's `documents`, or a forbidden field still refuses.

**The grant is re-read on every resolution**, so turning it off takes effect immediately, without
a restart, and applies to a proposal that is already pending: acceptance re-resolves the grant and
refuses with `mind_global_grant_missing`, writing nothing. The proposal stays `pending` rather
than being decided — the authority was withdrawn, not the change — so restoring the grant lets the
same proposal be accepted.

### What resolution actually checks

Every read and every apply, never cached:

1. the root is re-verified — it exists, is a directory, is enterable, and no component of it is
   a symlink;
2. every component **below** the root is walked with `lstat`, and a link anywhere fails closed;
3. the resolved real path must equal where it should have landed;
4. the target must be a readable regular file.

`realpath` alone would not do: it happily follows a link out of the vault and reports success,
because the file it lands on is real. The walk asks a different question.

A missing role is not permission to guess a filename. An unmapped `preferences` role refuses
even when `PREFERENCES.md` is sitting in the vault.

## Changing memory: propose, accept, apply

No model — local or cloud, now or later — writes durable memory. The path is
**proposal → explicit acceptance → hash-bound atomic apply** (D-2026-08-11-4).

```
  POST /api/mind/proposals          →  a row in SQLite. Zero Markdown writes.
                                       Records the target's current content hash.
        │
        │   (a person reads the proposed document and decides)
        ▼
  POST …/accept                     →  re-resolve · re-read · re-hash · compare
        │                                  ├─ differs → stale, nothing written
        │                                  └─ matches → atomic replace
  POST …/reject                     →  decided, and no document is touched
```

### What a proposal stores

Its Cofferdam-minted id and timestamps; the target as `scope` + `role` (+ the workspace it was
made in); the operation; the proposed document and its hash; **the base content hash**; the size
it was; one line saying why; and the provenance word.

It does **not** store a path, a root, a vault location, a provider session id, a model name, an
adapter id, a credential, or any reasoning transcript. The mapping from a role to a file is
configuration, re-read at apply time — a stored path would be a second authority holding a copy
that an edit to that configuration would silently invalidate.

`source` is assigned from the authenticated surface and is never a request field, for the reason
task `origin` is. In this milestone it can only be `user`.

### Hash-bound apply

Acceptance does not mean "write whatever was proposed earlier". Before a byte is written:

1. the proposal must be **pending**;
2. it must belong to the workspace that is active now — otherwise a workspace switch would land
   your edit in a different project's repository, and it would render perfectly well afterwards;
3. the role is resolved **again**, from configuration re-read at that moment, so a revoked
   grant, a removed mapping or a disabled project refuses with its own code;
4. the document on disk is re-read and re-hashed, and must still equal the recorded base.

A mismatch marks the proposal **stale** and writes nothing. There is no three-way merge and no
silent refresh: the diff you reviewed is not the diff that would land, so a person re-reads the
document and proposes again. A stale proposal never becomes a fresh one — **a new proposal is a
new review.**

### Atomic write

`mkstemp` in the target's own directory → the mode copied from the file being replaced → write →
`fsync` → `os.replace` → a best-effort `fsync` of the directory. The temporary file is in the
same directory because `os.replace` is only atomic within one filesystem.

No shell, no `git`, no `subprocess`, no `shutil`. On failure the temporary file is removed, the
target is byte-identical to what it was, and the proposal goes back to **pending** — because
recording it as applied would claim a change that is not on disk.

An apply changes **exactly one file**. Nothing else in the project or the vault is touched.

### No deletion, no creation

Not refused — **absent**. The operation vocabulary contains one word, `replace_document`, and
there is no function in the package that removes or creates a path. No delete, no rename, no
move, no `mkdir`, no recursion.

An **empty** proposed document is refused too, because a replace with nothing in it is a
deletion wearing a mutation's clothes.

PR2 modifies **existing** approved documents only. A mapped role whose file is missing refuses at
proposal time. Creating a file the host never wrote down would turn a document grant into a
directory grant, so **creating an approved-but-absent memory document is out of scope and needs
its own authority decision** — recorded as D-2026-08-12-2 rather than left as an implementation
gap.

### Lifecycle

| State | Means |
|---|---|
| `pending` | queued, nothing written, decidable |
| `applied` | written atomically against the hash it was reviewed against |
| `rejected` | a person said no; no document was touched |
| `stale` | the target drifted; the apply refused and wrote nothing |

Only `pending` can be decided. Accepting or rejecting anything else is a `409` naming the
current state — the same convention `task_already_finished` uses. A decided proposal's history
is not rewritten, and an applied proposal cannot be replayed.

`stale` is a **stored state** when an apply discovers drift, and a **derived flag** on every
read (`"stale": true`), computed fresh rather than cached — it is the fact somebody uses to
decide whether to press Accept, and a stored copy would be right for a few seconds and then
quietly wrong.

## Who may accept

**Only the private device-token surface.** All seven routes use `require_token`.

The Actions bridge holds a credential these routes have never heard of, so a bridge request is a
`401` because nothing here can recognise it — not because a check turns it away. That is the
structural statement D-2026-08-09-2 makes for Remote Control, and D-2026-08-11-4 requires it
here: the planner and the bridge have **no acceptance route at all**. The bridge application
exposes no memory operation of any kind, and PR2 does not modify the bridge.

There is no acceptance path through a task prompt, a worker adapter, a browser actuator, a
model-facing route or the Custom GPT. There is no planner in this milestone at all.

## The API

All seven routes are on the private device-token surface, over the tailnet, and every content
response carries `Cache-Control: no-store` — the body is somebody's own memory, which is the
same class of content as a task result.

| Route | Does |
|---|---|
| `GET /api/mind` | which roles are readable now, with size and hash; never a path |
| `GET /api/mind/documents/{scope}/{role}` | one approved document's content |
| `GET /api/mind/proposals` | queued changes, newest first, bounded, without content |
| `GET /api/mind/proposals/{id}` | one proposal, with its content and live staleness |
| `POST /api/mind/proposals` | queue a change — writes no Markdown |
| `POST /api/mind/proposals/{id}/accept` | hash-bound atomic apply |
| `POST /api/mind/proposals/{id}/reject` | refuse it |

`GET /api/mind` **never errors for an ordinary state.** No workspace active, no grant, a role
mapped to a file that is not there — each is a word in the payload on a `200`, because each is a
real state of a working host that a client has to render.

Bodies are closed vocabularies: an unknown key is a `422`, not a silent drop. `POST
/api/mind/proposals` accepts exactly `scope`, `role`, `content`, `reason` — no `workspace_id`
(the active workspace is the context, as it is for the objective), no `source`, no `base_hash`.
**Accept and reject accept no body fields at all**: everything about what is written was fixed
when the proposal was reviewed, and a field there would be a way to change the reviewed thing at
the moment of approval.

Semantic reason codes: `mind_scope_invalid`, `mind_role_invalid`, `mind_role_unconfigured`,
`mind_role_unavailable`, `mind_global_grant_missing`, `mind_content_invalid`,
`mind_reason_invalid`, `mind_proposal_unknown`, `mind_proposal_not_pending`,
`mind_proposal_stale`, `mind_proposal_workspace_changed`, `mind_apply_failed`.

`mind_role_unavailable` is deliberately one code covering missing, not-a-regular-file,
symlinked, escaped and unreadable. Publishing which it was would describe the host's filesystem
to a client one refusal at a time.

## This is local. Nothing leaves the host

Reading the mind is a **local** operation and this milestone adds no egress path of any kind: no
provider client, no bridge Action, no worker context, no projection, no prompt assembly. Nothing
here sends memory to Claude, the Agent SDK, the private Custom GPT, ChatGPT, a browser or any
other model.

D-2026-08-11-5 makes anything leaving the host a separate type — `CloudContextProjection` —
built by an explicit egress policy. **That type does not exist yet**, which is the honest state
to be in and the reason a caller cannot accidentally be in a different one.

## Storage

`$COFFERDAM_HOME/state/mind/mind.sqlite3` — its own database, beside `tasks.sqlite3` and
`workspace.sqlite3` and not inside either. Posture copied rather than re-decided: WAL,
`synchronous=FULL`, `foreign_keys=ON`, `0700` on the directory and `0600` on the database and
its WAL/shm siblings. Schema version 1; a database written by a newer build is **refused**
rather than downgraded.

**A read never creates the database.** On a host that has never proposed anything, listing
proposals answers from nothing — an empty list — and no file appears.

The rollback story is one sentence with one answer: **delete `state/mind/` and the host forgets
the pending proposals.** Every task, turn, workspace, objective and Markdown document is
untouched.

## What is not in this milestone

No Context Builder, `LocalContextPack` or `CloudContextProjection` — that is PR3. No automatic
document selection, token budgets, summarization, relevance scoring, prompt construction, worker
handoff context, vector search or backlink traversal. No `syncWorkspace` and no PWA panel (PR4).
No evidence bundles (M2K). No planner, model runtime or Ollama (M2L). No browser work. No new
adapter, no provider routing, no change to `delegated_adapter`. No public surface, and no change
to the Actions bridge.
