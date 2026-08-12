"""What may be in a pack, in what order, and how much of each.

Separated from the builder because these are **decisions**, not mechanism. Each
constant below traces to a line in `ROADMAP.md` or `DECISIONS.md`, and changing
one is a change to what Cofferdam reads about a person — which should look like
a diff to a policy file rather than an adjustment inside a loop.

The order
---------

Recorded in `ROADMAP.md` under M2J PR3, and implemented literally:

1. the user's current message — never truncated;
2. Working Context;
3. workspace/project status;
4. the relevant plan section;
5. recent decisions;
6. the latest evaluation summary;
7. bounded global style and preference extracts.

**Position 6 has no source.** M2K's evidence and evaluation do not exist, so
every pack records an omission there rather than skipping the slot or, worse,
inventing something to fill it. No evaluator was written to satisfy a priority
position.

Retrieval candidates enter after 7, at the bottom. That placement is honest for
a build with no retrieval in it and is M2N's to revisit when it has a producer.

Why `design` is not here
------------------------

It is mapped on this host, readable, and useful. It is also not in the recorded
priority order, and widening what Cofferdam reads about a project because a
document happened to be available is exactly the drift this file exists to make
visible. Adding it is a policy change with a test.

Why only two global roles
-------------------------

The recorded order names **"bounded global style/preference extracts"** and
nothing else. `user` and `cross_project` are granted and readable on the
production host, and they are still excluded, because no current canonical rule
authorizes putting a person's identity document into every request. That is a
gap to close deliberately in a later PR — with a decision — rather than an
inclusion to widen quietly here, and the same reasoning D-2026-08-11-5 applies
to the egress projection applies inside the host.
"""

from __future__ import annotations

from typing import Dict, Tuple

from .kinds import KIND_DECISION, KIND_MEMORY, KIND_PLAN

#: Project roles this build reads, in priority order. A closed list: the builder
#: never iterates "whatever roles are mapped".
PROJECT_CONTEXT_ROLES: Tuple[str, ...] = ("status", "plan", "decisions")

#: Global roles this build reads. See the module docstring for the two that are
#: readable and deliberately absent.
GLOBAL_CONTEXT_ROLES: Tuple[str, ...] = ("communication_style", "preferences")

#: What kind of truth each role's material is.
ROLE_KINDS: Dict[str, str] = {
    "status": KIND_MEMORY,
    "plan": KIND_PLAN,
    "decisions": KIND_DECISION,
    "communication_style": KIND_MEMORY,
    "preferences": KIND_MEMORY,
}

#: Which Working Context field, if set, names a section of which role. This is
#: the only explicit relevance signal PR3 has, and it is the right one: it is a
#: reference a **person** recorded, through a validated route, about the work
#: they are actually doing. PR1 left these fields opaque with nothing resolving
#: them; this is the reader they were reserved for.
EXPLICIT_REFERENCE_FIELDS: Dict[str, str] = {
    "plan": "plan_checkpoint",
    "decisions": "pending_decision_ref",
}

#: Roles whose documents are **append-ordered**, so "recent" means the end.
#: `DECISIONS.md` appends; everything else reads from the top. This is a
#: documented structural convention, not an inference about content — and it is
#: per-role rather than guessed per-document, so no heuristic decides it.
APPEND_ORDERED_ROLES: Tuple[str, ...] = ("decisions",)

#: The whole pack, in UTF-8 bytes. Comfortably larger than everything below it
#: added together, so the total binds only when a message is large or a document
#: is unusually long — the cases where a bound is the point.
DEFAULT_TOTAL_BUDGET_BYTES = 64 * 1024

#: Per-source ceilings, applied before the remaining total. A source cannot take
#: the whole budget just because it came earlier in the order, which is what
#: keeps a 120 KB `DECISIONS.md` from crowding out everything after it.
#:
#: The global extracts are the tightest on purpose. "Bounded extracts" is the
#: recorded wording, and a smaller ceiling for personal memory than for project
#: memory is the correct default when the two are in the same object.
SOURCE_CAPS: Dict[str, int] = {
    "workspace:working_context": 4 * 1024,
    "project:status": 8 * 1024,
    "project:plan": 12 * 1024,
    "project:decisions": 12 * 1024,
    "global:communication_style": 4 * 1024,
    "global:preferences": 4 * 1024,
    "retrieved": 4 * 1024,
}

#: The reference recorded for the priority slot M2K will one day fill.
EVALUATION_SLOT_REF = "evaluation:latest"


def cap_for(key: str) -> int:
    return SOURCE_CAPS[key]


__all__ = [
    "APPEND_ORDERED_ROLES",
    "DEFAULT_TOTAL_BUDGET_BYTES",
    "EVALUATION_SLOT_REF",
    "EXPLICIT_REFERENCE_FIELDS",
    "GLOBAL_CONTEXT_ROLES",
    "PROJECT_CONTEXT_ROLES",
    "ROLE_KINDS",
    "SOURCE_CAPS",
    "cap_for",
]
