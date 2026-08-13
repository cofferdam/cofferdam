# Local context assembly

M2J PR3. What Cofferdam gathers to answer *"given what you just said and what
this host currently knows, what context is appropriate?"* — and why every part
of that answer stays on the machine.

Decided in [`DECISIONS.md`](../DECISIONS.md) D-2026-08-11-3, D-2026-08-11-5,
D-2026-08-12-4 and D-2026-08-13-2; scoped in [`ROADMAP.md`](../ROADMAP.md) under
M2J. Reads through
the workspace model in [`docs/WORKSPACES.md`](WORKSPACES.md) and the memory
model in [`docs/MIND.md`](MIND.md), and adds no authority of its own.

## What a `LocalContextPack` is

An ordered, bounded, fully-accounted list of typed parts, assembled on demand:

```
current user message
        +  Working Context
        +  role-addressed Project Mind
        +  policy-approved Global Mind
                    ↓
        deterministic Context Builder
                    ↓
            LocalContextPack
```

Every part says four things about itself: **what kind of truth** it is, **where
it came from** as a semantic reference, **when it was observed**, and **how it
was selected**. Alongside the parts the pack carries the budget it was built
under and one row for every source that is *not* in it, with a reason.

It is a **value**, not a record. Nothing stores it, caches it, indexes it or
reuses it. Build the same pack twice and you get two packs.

## What it is not

**Not a prompt.** There is no system string, no message array, no role field, no
template and no provider anywhere in the package. PR3 stops at structured local
context; turning context into a request belongs to the planner (M2L), which does
not exist.

**Not a `CloudContextProjection`.** D-2026-08-11-5 makes the pack that *leaves*
the host a separate type built by an explicit egress policy. That type was not in
the PR3 build, and PR3 adds no path toward one — no provider client, no bridge
Action, no worker context, no serializer to a wire format. The local pack is
allowed to be rich precisely because nothing can send it.

**M2J PR3.5 built it** (D-2026-08-13-3), and the separation is unchanged: the
projection is a different type, produced only by an explicit policy step, and
nothing on this page grants egress to anything. See
[`docs/CLOUD_CONTEXT_PROJECTION.md`](CLOUD_CONTEXT_PROJECTION.md).

**Not retrieval.** Selection is either a reference somebody recorded or a bounded
structural slice, and every part is labelled with which. D-2026-08-12-4 makes
semantic retrieval a **required** Mind capability — and requiring it is exactly
why it is not approximated here. A keyword heuristic labelled "relevant" would
be believed by everything downstream and would be wrong in a way nobody could
see. M2N supplies real candidates; see [the seam](#the-m2n-seam).

**Not a filesystem authority.** The builder holds no path, no root and no reader.
It asks `MindService` for a **role** and `WorkspaceService` for the snapshot. An
unmapped vault note, `.obsidian/`, a document outside a granted root and an A/B
slot are not *filtered* — they are unreachable, because there is no way to name
one.

**Not a writer.** No Markdown write, no memory proposal, no task, no objective,
no Working Context field. Reading memory to answer a question must not change
the memory or the state.

## Source priority

The order recorded in `ROADMAP.md`, implemented literally:

| # | Source | `source_kind` | Selection |
|---|---|---|---|
| 1 | the current user message | `user_instruction` | whole, **never trimmed** |
| 2 | Working Context | `working_state` | whole |
| 3 | project `status` | `memory` | explicit or structural |
| 4 | project `plan` | `plan` | explicit or structural |
| 5 | project `decisions` | `decision` | explicit or structural |
| 6 | *latest evaluation summary* | — | **no source exists** |
| 7 | `global:communication_style`, `global:preferences` | `memory` | structural |
| 8 | retrieval candidates | supplied | retrieved (**none in this build**) |

**Position 6 is empty and says so.** M2K's evidence and evaluation do not exist,
so every pack carries an omission row for `evaluation:latest` with the reason
`source_not_in_this_build`. No evaluator was written to fill a priority slot and
nothing is fabricated to stand in for one.

## The budget is UTF-8 bytes

`DECISIONS.md` requires a bounded pack and does not name a unit. The unit is
**UTF-8 bytes**, and the reasoning is that the alternative is worse in two
directions at once: a token count would make a model-free component depend on a
provider's tokenizer, and it would make the same pack cost a different amount on
a different model. Bytes are defined by the encoding, identical on every host,
and already this repository's unit for a document (`MAX_DOCUMENT_BYTES`,
`base_bytes`, the `bytes` field on every mind payload).

- **Total:** 64 KiB by default, overridable per build.
- **Per source:** Working Context 4 KiB · status 8 KiB · plan 12 KiB ·
  decisions 12 KiB · each global extract 4 KiB · each candidate 4 KiB.
- **Counted:** `len(part.text.encode("utf-8"))`, summed. Metadata is not counted
  and is bounded by construction.
- **Reported:** unit, total, consumed and remaining, on every pack.

The per-source ceilings are what stop a 120 KB `DECISIONS.md` crowding out
everything after it. The global extracts have the tightest ceilings on purpose:
"bounded style/preference extracts" is the recorded wording, and personal memory
should have a smaller allowance than project memory when both are in one object.

### When the message alone is too big

The build **refuses** — `CurrentMessageOversize`, and **no pack is returned**.

Not trimmed: the user's own sentence is authored text, and silently storing half
of it is the failure the workspace objective and the memory-proposal reason
already refuse. Not partially answered either: a pack missing its highest-priority
part would be rendered by something downstream with nothing in it saying so.
And obviously not summarised — there is no model here, and there is not meant to
be one.

## Selection: explicit, structural, and the third one that does not exist yet

| Mode | Meaning |
|---|---|
| `whole` | the whole value — the message and Working Context |
| `explicit` | a section a **stored reference chose by name** |
| `structural` | a bounded slice chosen by **position in the document** |
| `retrieved` | supplied by a retrieval component — **no producer in this build** |

### Explicit

The only relevance signal PR3 has, and the right one: a reference a *person*
recorded through a validated route about the work they are actually doing. PR1
reserved `plan_checkpoint` and `pending_decision_ref` as opaque strings with
nothing resolving them; this is the reader they were reserved for.

- `plan_checkpoint` selects a section of the `plan` role.
- `pending_decision_ref` selects a section of the `decisions` role.

Matching is literal: take whatever follows the last `#` if there is one, slug it
the same way a heading is slugged, and compare for equality. No fuzzy matching,
no prefix matching, no scoring.

**A reference that names no section omits the role.** It is recorded as
`explicit_section_missing`, and **no structural substitute is put in its place** —
a substitute would answer a question nobody asked and would look identical
afterwards. `latest_evidence_ref` selects nothing, because nothing resolves
evidence yet.

### Structural

With no reference, whole sections are taken until the next one would not fit:
from the **top** for an ordinary document, and from the **end** for an
append-ordered one. `decisions` is the only append-ordered role, which is how
"recent decisions" is implemented without any judgement about content. Output is
always in document order.

If not even one section fits, the text is cut at a character boundary and the
part is marked `truncated`. Nothing is summarised, reordered or rewritten, and
the canonical document is never touched.

### The section reader

CommonMark ATX headings and nothing else. Fenced code is skipped, so the JSON
examples with `#` comment lines in Cofferdam's own documents do not shred the
outline. Text before the first heading is kept. Repeated headings — which a real
`DECISIONS.md` has — get `-2`, `-3`, so the second `## Notes` is addressable and
is never silently the first.

A `section_id` is restricted to `[a-z0-9-]`, and that restriction is load-bearing:
it is what stops a heading like `## ../../etc/passwd` putting a path-shaped
string into a pack's provenance. Diacritics fold rather than drop
(`## Görüşler` → `gorusler`) so Turkish headings stay addressable; a heading that
folds to nothing falls back to its position.

`[[Wikilinks]]` are ordinary characters. They are preserved verbatim as content
and **never followed** — backlink traversal is M2N.

## Provenance

Every part carries `{source_kind, source_ref, observed_at}` plus how it was
selected.

`source_kind` is one of nine code-owned words. **Five are producible here** —
`user_instruction`, `working_state`, `plan`, `decision`, `memory` — and four
(`worker_result`, `machine_observed`, `external_model_output`,
`planner_inference`) are declared and structurally unreachable until the systems
that would be their authority exist. A part claiming one is refused at
construction, not filtered later.

`source_ref` is a **semantic address, never a location**:

```
user:current_message
workspace:cofferdam:working_context
project:cofferdam:plan
project:cofferdam:plan#m2j
global:communication_style
```

It answers *which authority produced this*, and the authority is a role. That
makes the reference stable across a moved checkout, a renamed file and an A/B
slot flip, and it tells a reader nothing about the host's filesystem. A separator,
a home marker, a parent segment or an unknown scheme is **refused at
construction**, so a reference that could leak a location never exists to be
filtered.

`observed_at` is when Cofferdam **read** the source, never when the document was
written. Context assembly reads current canonical state; there is no cache, so
there is nothing that could be stale.

## Nothing is dropped silently

Every source in the policy is visited and every visit ends in one of two lists.
The reasons:

| Reason | Meaning |
|---|---|
| `no_active_workspace` | nothing below the message has an authority |
| `source_absent` | the role is unmapped, or the project is missing or disabled |
| `grant_absent` | the global vault is not granted, or not turned on |
| `source_unreadable` | mapped and unreadable now — missing, not a file, oversized, not UTF-8 |
| `source_empty` | readable and blank |
| `explicit_section_missing` | a recorded reference named a section the document does not have |
| `budget_exhausted` | no budget remained when this source's turn came |
| `source_not_in_this_build` | the priority slot exists and the system that fills it does not |

Truncated parts keep their provenance, are marked `truncated`, and never mutate
the source.

## Global Mind policy

### Three permissions, never one

D-2026-08-13-2 is the decision this section implements, and its whole point is
that these are separate questions with separate answers:

| | Question | Decided by |
|---|---|---|
| **Read authority** | may Cofferdam open this at all? | the host-owned grant and the role map |
| **Context inclusion** | should this be in *this* pack? | context policy — this page |
| **Egress permission** | may this leave the host? | [`CloudContextProjection`](CLOUD_CONTEXT_PROJECTION.md), D-2026-08-11-5 |

> **Mind read authority ≠ automatic context inclusion.**
> **`LocalContextPack` inclusion ≠ `CloudContextProjection` permission.**

Cloud egress is an entirely separate policy and nothing on this page touches it.
A part being in a local pack says nothing whatsoever about whether it may leave
the machine — and the egress policy proves it, by denying `communication_style`
and `preferences` even though this page includes both.

### The default eligible roles

**`communication_style` and `preferences`.** These are automatically eligible
whenever the grant is active and the role is mapped.

**`user` and `cross_project` are not automatically injected.** On the production
host both are granted, mapped and readable — and the builder still excludes
them. Granting all four roles does not widen the pack, and a test asserts that
directly.

That is intentional, not an unfinished edge:

- a pack should carry context appropriate to the **current interaction**, not
  every piece of locally accessible memory;
- `USER.md` may eventually hold broad personal information irrelevant to most
  requests;
- `CROSS_PROJECT.md` is especially prone to context pollution when the active
  workspace concerns one project;
- being *allowed* to read a role is not a reason for every planner request to
  receive it.

### How the excluded roles are meant to arrive

Not by widening the default set, and **not by a heuristic**. `user` enters
through an explicit local policy or reference when it is relevant;
`cross_project` primarily through an explicit reference or, later, M2N semantic
retrieval when another project's memory is *actually* related.

Either way the material arrives as a typed [`RetrievedCandidate`](#the-m2n-seam)
and goes through the same budget, provenance and omission machinery as
everything else — retrieval never bypasses it, and `LocalContextPack` does not
need redesigning to accept it. There is deliberately **no keyword heuristic**
guessing when these roles are relevant: an approximation labelled "relevant"
gets believed, which is the failure D-2026-08-12-4 exists to prevent.

Widening the default set is a change to D-2026-08-13-2, with a test.

### The grant is still the gate

No `config/mind-grant.json`, or `enabled` not literally `true`, means no global
material at all and a `grant_absent` row (D-2026-08-12-2). Revoking it between
two builds takes effect on the second one, because the grant is re-read every
time.

## Project Mind policy

Three roles: `status`, `plan`, `decisions`. `design` is mapped on this host,
readable, and deliberately not included — it is not in the recorded priority
order.

Reads go through `MindService` by **role**. The builder never sees a filename,
so re-mapping `plan` to a different document changes what the pack contains
without any change here, and a role mapped to nothing has no file at all.

## Privacy and logging

**Nothing is logged.** Not "logged carefully" — the package emits no log records
at all, which is also what the mind and workspace packages do.

For the caller that wants a journal line, `pack.summary()` returns structural
facts only: part count, source kinds, source references, byte totals, truncation
count and omission reasons. It carries no message, no objective, no document
text and no heading. The split is the point: the method that would leak is not
the method whose name suggests logging.

## The M2N seam

`build()` accepts `candidates: Sequence[RetrievedCandidate]`. Nothing in this
build supplies any.

When M2N exists, its retrieval output arrives as candidates already carrying
provenance, is validated like any other part, and is budgeted last:

```
deterministic direct sources  +  M2N retrieval candidates
                    ↓
            Context Builder
                    ↓
            LocalContextPack
```

That is the entire extension boundary — one typed parameter, no registry, no
plugin framework, and no way for a retrieval component to widen a pack without
going through the same accounting as everything else.

## API

**None.** PR3 adds no HTTP route, to the workstation or to the Actions bridge,
and no PWA surface. Surfaces are PR4's scope; the Actions bridge route list and
the OpenAPI document are byte-for-byte unchanged.

The Context Builder is an internal service object:

```python
builder = ContextBuilder(workspaces=workspace_service, mind=mind_service)
pack = builder.build("what should I do next?")
```

## What is not in this milestone

- `CloudContextProjection`, and any egress of any kind. That is **PR3.5**, which
  is merged and deployed, and which gated PR4 (D-2026-08-13-3). Nothing in *this*
  milestone sends anything anywhere.
- Any provider, model runtime, tokenizer or local model.
- Embeddings, vectors, semantic retrieval, links or backlinks traversal (M2N).
- Evidence, evaluation and the `EvidenceBundle` (M2K).
- The planner (M2L), and any prompt construction.
- Persistence or caching of a pack, in any form.
- Any change to the Mind proposal/apply path, Task Core, or `delegated_adapter`.
