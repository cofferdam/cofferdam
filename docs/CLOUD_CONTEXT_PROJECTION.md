# The egress boundary

M2J PR3.5. What may leave this machine, decided by one narrow policy that denies
by default — and an honest account of what that policy can and cannot prove.

Decided in [`DECISIONS.md`](../DECISIONS.md) D-2026-08-11-5 and D-2026-08-13-3,
resting on D-2026-08-13-2; scoped in [`ROADMAP.md`](../ROADMAP.md) under M2J.
Reads a `LocalContextPack` from [`docs/CONTEXT.md`](CONTEXT.md) and adds no
authority of its own.

## Why there are two objects

```
LocalContextPack            rich, local, unsendable
        ↓
project_context_external_v1        the egress policy
        ↓
CloudContextProjection      bounded, reduced, eligible
        ↓
[ an explicitly authorized surface — PR4, not in this build ]
```

D-2026-08-11-5 made these two types rather than one, and the reasoning is worth
repeating because it is the whole design: **one universal `ContextPack` would
make every future caller a potential egress path, and the mistake would be
invisible.** A field added for the planner would reach the Custom GPT the same
day, through a diff that looked correct. Two types make the boundary a
compile-time question instead of a review question.

So a `LocalContextPack` is **not structurally cloud-authorized**. There is no
method on it that produces a projection, no `from_pack` constructor, no
serializer to a wire format and no helper anywhere in the repository. The only
route is `ContextProjector.project`, which means "which policy produced this
object" always has an answer.

## Three permissions, and this page owns the third

D-2026-08-13-2, restated once more because collapsing any two of these is the
failure mode:

| | Question | Decided by |
|---|---|---|
| **Read authority** | may Cofferdam open this at all? | the host-owned grant and the role map |
| **Context inclusion** | should this be in *this* pack? | context policy — [`docs/CONTEXT.md`](CONTEXT.md) |
| **Egress permission** | may this leave the host? | **this page** |

> **Mind read authority ≠ automatic context inclusion.**
> **`LocalContextPack` inclusion ≠ `CloudContextProjection` permission.**

On the production host `communication_style` and `preferences` are all three of
granted, mapped and **present in every pack**. This policy denies both.

## Eligibility is decided on the reference, not the kind

The single most important implementation detail, and the one a reasonable
person gets wrong: `global:preferences` and `project:cofferdam:status` are
**both** `source_kind == "memory"`.

A policy keyed on the kind would publish the operator's personal preferences the
first time somebody wrote `if part.source_kind == KIND_MEMORY`, and the diff
would pass review. So the projector decomposes the **semantic reference** —
scheme, identity, role — and then requires the kind to *agree* with it.

A part whose kind and reference tell different stories is refused as
`source_kind_mismatch` rather than resolved by believing one half. It is either a
producer bug or an attempt to smuggle, and neither is fixed by guessing.

## What may leave

| Source | Reference | Required kind |
|---|---|---|
| Working Context | `workspace:<id>:working_context` | `working_state` |
| project status | `project:<id>:status[#section]` | `memory` |
| project plan | `project:<id>:plan[#section]` | `plan` |
| project decisions | `project:<id>:decisions[#section]` | `decision` |

The identity must match the pack's own workspace and project. Another project's
memory in the same pack is not eligible, however it got there.

### Working Context is re-rendered, never copied

Four fields, projected from PR3's **structured `fields`**:

| Field | Why |
|---|---|
| `objective` | what we are trying to do |
| `expected_next_step` | what we think is next |
| `plan_checkpoint` | where a person said the plan stands |
| `pending_decision_ref` | which decision a person said is open |

Everything else is denied by name: `delegated_worker`, `delegation` and
`active_task` are adapter, provider and Task Core internals; `revision` and the
`objective_*` metadata are Cofferdam's own bookkeeping; `latest_evidence_ref`
names a system that does not exist (M2K) and would be an unresolvable reference
on the wire.

**The rendered text is never parsed.** It cannot be: the builder renders it *from
those same fields*, so it already contains `delegated worker:` and `active task:`
lines. Projecting from structure rather than from a rendering is what makes the
allowlist an allowlist. A test proves it by corrupting the rendered text and
watching the correct values survive anyway.

## What may not

- **Global Mind, all four roles** — `user`, `communication_style`, `preferences`,
  `cross_project`. Useful to the local planner; that does not make them
  cloud-safe. Proved by a unique sentinel in each of the four vault documents and
  a search of the **whole serialized projection**, so the claim is about bytes
  rather than about structure.
- **The current user message.** A PR4 Custom GPT surface already has the user's
  conversation. Worker handoff is a different egress profile and a different
  problem, and PR3.5 is deliberately not generalized into prompt construction.
- **`design`** — mapped and readable on this host, in no pack, and not introduced
  here. Adding a role to an egress policy because the document happens to exist
  is exactly the drift the policy file exists to make visible.
- Any other project, any other workspace, the evaluation slot, and **every scheme
  the profile cannot classify**. Unknown fails closed.

## The content is a separate problem from the metadata

PR3's production validation established the fact this section exists for:
**canonical Markdown legitimately contains local strings even when Cofferdam's
own metadata is clean.** Real decisions in this repository discuss `slots/a`,
`slots/b`, project roots and operational paths, because those are what the
decisions are *about*.

A semantic `source_ref` therefore proves nothing about the text beneath it, and
a projection is not safe merely because its references are.

### Two outcomes, chosen by consequence

**Recognised local paths are replaced** with a visible placeholder, and
`path_redacted` is recorded on the part. A decision that says "deployment flips
between `slots/a` and `slots/b`" is *about* something a reader needs; dropping it
would lose the meaning to protect a detail.

**Credential-shaped material omits the whole part.** No rewrite, no partial, no
masking of the middle. A redaction is a guess about which bytes mattered, and a
wrong guess about a secret is permanent and unobservable. The part is dropped and
the omission is recorded.

### What is deliberately *not* treated as a path

`/api/tasks` is a route this product documents constantly.
`state/tasks/tasks.sqlite3` is a relative name. `https://example.com/home/x` is
an address. Treating any of them as filesystem authority would shred canonical
text to no benefit, so the patterns are anchored on things that genuinely are
roots — `~/`, `/home/<user>`, `/root/`, `slots/a|b`, `.obsidian` — and URLs are
masked out of range while the generic patterns run.

The exception is deliberate: a **known host literal** the caller supplied is
redacted everywhere, *including* inside a URL. The URL exemption is about generic
shapes, not about a value somebody explicitly said must never be emitted.

### Separators are runs, not single characters

M2J PR3.5.1. POSIX collapses a run of slashes, so `/home//someone/x` and
`/home/someone/x` name the same file. Until PR3.5.1 the patterns accepted exactly
one slash between components, and the known host literals were a plain substring
test — which cannot see a separator the operator did not type. Both accept a run
now, and `//api/tasks` is still a route rather than a location, because what makes
a path here is the anchor at the front and never the number of slashes in the
middle.

`https://` is untouched by this: the scheme separator is matched by the URL rule
before the path patterns run, and a run of separators is never rewritten in the
text — only the whole path it belongs to is replaced.

## The sanitizer is not the boundary

**Pattern matching cannot prove that arbitrary text contains no secret, and
nothing in this repository should be written as though it can.**

The protection is layered, and only two of the layers are recognition:

| Layer | Kind |
|---|---|
| narrow source allowlist | construction |
| Global Mind excluded by default | construction |
| structured Working Context field allowlist | construction |
| semantic reference grammar | construction |
| bounded output | construction |
| known-host-value redaction | recognition |
| conservative secret detection → fail-closed omission | recognition |

If the sanitizer recognised nothing at all, personal memory, the user's message,
unrelated projects, filesystem references and unbounded output would **still** be
impossible. That is the design: recognition is the last layer, not the first.

### Known residual limitations

Carried on every projection and asserted as passing tests in
`tests/test_cloud_projection_adversarial.py`, so they stay true statements rather
than becoming stale caveats:

- a credential in an unrecognised shape, or a passphrase in prose, is not
  detected;
- a credential **variable name is matched in upper case only**, as environment
  variables are written. `api_key=…` in lowercase prose is not detected. Lowering
  the case would widen a rule whose consequence is dropping a whole eligible
  part, so this is recorded rather than fixed by reflex;
- a secret split across a **line break** is not reassembled, because reassembling
  lines would change what a Markdown document means;
- a relative path with no recognised root is not treated as a location;
- Windows-style paths are not recognised — this product's hosts are Linux;
- prose that *describes* where something lives is content, and is not redacted.

### Fixed in M2J PR3.5.1

Both were found by the PR3.5 **post-deployment** validation, before any surface
could transmit a projection, and neither was ever externally reachable: nothing
in this build imports the projection package. They are recorded here rather than
quietly corrected because this page's residual-limitations list is only worth
reading if what left it is visible too.

- **Bare credential assignments were not detected.** The variable-name pattern
  opened with a mandatory character, so `COFFERDAM_ACTIONS_TOKEN=` matched and a
  bare `TOKEN=`, `API_KEY=`, `SECRET=`, `PASSWORD=`, `AUTH=` or `PRIVATE_KEY=`
  did not — the prefix is the part that varies between hosts, and requiring it
  inverted which half carried the meaning. The prefix is optional now. The value
  test is unchanged, so documentation placeholders are still kept.
- **A doubled slash bypassed every path rule.** `/home//x`, `/root//x` and
  `slots//a` survived, and a known host literal with an internal doubled
  separator defeated literal redaction as well. Separators are runs now.

Neither fix changes the outcome for material that *was* recognised: a
credential-shaped part is still omitted **whole**, with an explicit
`sensitive_content_omitted`, and a recognised path is still replaced in place
with `path_redacted` recorded on the part.

## The budget is its own number

**16 KiB of UTF-8**, against the local pack's 64 KiB.

Not inherited, on purpose. One number governing both *how much may the planner
see* and *how much may leave the machine* is a number a later tuning change
widens by accident.

- **Per slot:** Working Context 2 KiB · status 4 KiB · plan 5 KiB ·
  decisions 5 KiB.
- **Counted:** `len(part.text.encode("utf-8"))`, summed after sanitization.
- **Reported:** unit, total, consumed, remaining.

16 KiB survives JSON escaping, structural metadata and transport framing with
comfortable room left, and it encodes **no provider's wire limit**: there is no
tokenizer here, no model, and no assumption about which destination a later
authorized surface picks.

## Nothing is dropped silently

Every part of the pack ends in exactly one of two lists.

| Reason | Meaning |
|---|---|
| `policy_excluded` | not eligible under this profile |
| `source_kind_mismatch` | the kind and the reference disagree |
| `source_ref_unsupported` | a shape this profile cannot classify |
| `sensitive_content_omitted` | matched a credential shape; not rewritten |
| `source_empty` | nothing remained, or nothing was there |
| `budget_exhausted` | no egress budget remained at this source's turn |
| `duplicate_part` | an identical part was already projected |

And one transformation, recorded on the part itself: `path_redacted`.

## The M2N re-check

A retrieval candidate reaches a pack as an ordinary part, so it reaches the
projector as one too, and it is classified by the same call as everything else.

```
M2N candidate → LocalContextPack → egress policy AGAIN → CloudContextProjection
```

There is **no candidate branch**, no "already vetted" flag and no way for a
retrieval component to mark its own output cloud-safe. Admission to a pack and
admission to a projection are decided twice, by two policies, from two different
questions. M2N is not implemented; this is the seam it will meet.

## Provenance and logging

Every projected part keeps `{source_kind, source_ref, observed_at, selection}`
plus its redaction codes. No section object survives — `section_id` is
slug-restricted and rides inside `source_ref`, but the `heading` it came from is
raw document text and is dropped.

`summary()` is **narrower than the pack's**, which lists its source references. A
projection's reference can carry a section slug, a slug is derived from a
heading, and a heading is the operator's own words about their own project. So
the summary reports counts, kinds, byte totals, redaction counts and omission
reason counts — and never which document said what. **Nothing is logged by this
package at all.**

## Eligibility is not transport

A `CloudContextProjection` performs **no network activity**, and holding one is
not permission to send it.

"Cloud" names what the object is *shaped for*, not where it goes. Projection is
destination-independent: it does not know about Claude, the Agent SDK, a Custom
GPT, ChatGPT or any other provider, and it does not choose between them.

A surface that transmits one still owns everything projection deliberately does
not: **authentication, authorization, a named destination contract, and the
user-consequence semantics** that go with a request that has effects.

## The surface that consumes this (M2J PR4)

`GET /api/projects/{project_id}/context` on the workstation, and
`getProjectContext` on the Actions bridge. Both return a **serialized
`CloudContextProjection`** and nothing else; `serialize_project_context` refuses
any other type, including a `LocalContextPack`, which duck-types past a looser
check because it also has `to_dict`, `version` and `parts`.

- **`project_id` reads the active workspace only.** It resolves to the one
  *enabled* workspace naming that project, and that workspace must be the active
  one — otherwise `workspace_not_active`. The builder is active-workspace-scoped
  by PR3's design, and letting a caller name any workspace would hand it the
  choice of whose memory is read.
- **The pack is built without a user message** (D-2026-08-13-5), so there is no
  `user:current_message` part to exclude rather than a fake one to deny.
- **`HostRedactionEnvironment` is host-owned**: operational home, the
  registry-resolved project root, the granted vault root, both A/B slot roots.
  `.none()` is never called on the request path, and a test asserts that on the
  parsed tree rather than by scanning text.
- **The serialized body is capped at 128 KiB** and **refused, never trimmed** —
  `response_too_large`. That is a transport bound on top of this page's 16 KiB
  content budget, because 16 KiB of content can reach 96 KiB once every character
  escapes to `\uXXXX`. Slicing JSON to fit produces something that is not JSON,
  and dropping parts there would be a second egress policy underneath the named
  one.
- **Read-only.** No task, no event, no Working Context revision, no proposal, no
  write. Repeating the call is free, so it carries no idempotency key.

`syncWorkspace` is **not** here and is not PR4's — see D-2026-08-13-4.

## The invariant that gates PR4

> **No external surface may return a `LocalContextPack`, or anything derived from
> one, except a `CloudContextProjection` produced by a named egress policy.**

D-2026-08-13-3. PR4 owns surfaces and is held to this.

## What is not in this milestone

- Any transport, route, Actions bridge operation, OpenAPI change or PWA surface.
- Any provider, model runtime, tokenizer or local model. No model makes any
  decision here.
- Embeddings, vectors, backlinks or semantic retrieval (M2N).
- Evidence and evaluation (M2K); the planner (M2L); the dashboard (M2M).
- Any connector — Notion, Google Drive or otherwise. Nothing assumes provenance
  points at local Markdown, and every future external source would still have to
  pass this policy before being forwarded onward.
- Persistence, caching or a projection history of any kind.
- A workspace policy override permitting selected Global Mind extracts. Allowed
  later by D-2026-08-11-5; **not built here**, and today's default stays
  exclusion.
