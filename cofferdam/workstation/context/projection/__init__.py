"""The egress boundary: the only typed project context eligible to leave the host.

**M2J PR3.5.** D-2026-08-11-5 made local context and external context two
security objects. PR3 built the first one. This package builds the second, and
the gap between them is the entire point:

```
LocalContextPack          rich, local, unsendable
        ↓
CloudContextProjectionPolicy   project_context_external_v1
        ↓
CloudContextProjection    bounded, reduced, eligible
        ↓
[ an explicitly authorized surface — not in this build ]
```

Three permissions, and this package owns the third
--------------------------------------------------

D-2026-08-13-2 separates *may Cofferdam read this* (the grant and the role map),
*should this be in this pack* (:mod:`..policy`), and *may this leave the host*
(here). None implies the next. A granted Global Mind role is not automatically in
a pack, and a part in a pack is not automatically in a projection — on the
production host, `communication_style` and `preferences` are all three of granted,
mapped and present in every pack, and this policy denies both.

What this package does not do
-----------------------------

**No network.** Nothing here opens a socket, imports a client or names a
destination. "Cloud" describes what the object is *shaped for*, not where it
goes: a projection is destination-independent egress preparation, and holding one
is not permission to transmit it. A future surface still needs its own
authentication, authorization and destination contract.

**No surface.** No HTTP route, no Actions bridge operation, no OpenAPI change, no
PWA panel, no WebSocket event. Surfaces are PR4's scope, and PR4 is gated on this
package existing.

**No model, no retrieval, no persistence.** Every decision is a closed vocabulary
lookup, a regular expression and a byte count. Nothing is stored, cached or
logged.

The invariant this exists to make enforceable
---------------------------------------------

> No external surface may return a `LocalContextPack`, or anything derived from
> one, except a `CloudContextProjection` produced by a named egress policy.

See [`docs/CLOUD_CONTEXT_PROJECTION.md`](../../../../docs/CLOUD_CONTEXT_PROJECTION.md).
"""

from __future__ import annotations

from .errors import (
    CODE_PROJECTION_BUDGET_INVALID,
    CODE_PROJECTION_INPUT_INVALID,
    ProjectionBudgetInvalid,
    ProjectionError,
    ProjectionInputInvalid,
)
from .model import (
    BUDGET_UNIT,
    LIMITATIONS,
    CloudContextPart,
    CloudContextProjection,
    ProjectionBudget,
    ProjectionOmission,
    encoded_length,
)
from .policy import (
    ALLOWED_PROJECT_ROLES,
    ALLOWED_WORKING_CONTEXT_FIELDS,
    DEFAULT_PROJECTION_BUDGET_BYTES,
    DENIED_WORKING_CONTEXT_FIELDS,
    OMISSION_REASONS,
    OMIT_BUDGET_EXHAUSTED,
    OMIT_DUPLICATE_PART,
    OMIT_POLICY_EXCLUDED,
    OMIT_SENSITIVE_CONTENT,
    OMIT_SOURCE_EMPTY,
    OMIT_SOURCE_KIND_MISMATCH,
    OMIT_SOURCE_REF_UNSUPPORTED,
    PROJECTION_API_VERSION,
    PROJECT_CONTEXT_EXTERNAL_V1,
    REDACTION_PATH,
    REDACTIONS,
    REQUIRED_PROJECT_KINDS,
    SOURCE_CAPS,
)
from .sanitizer import (
    PLACEHOLDER,
    RESIDUAL_LIMITATIONS,
    HostRedactionEnvironment,
    Sanitized,
    sanitize,
)
from .service import MAX_FIELD_CHARS, ContextProjector

__all__ = [
    "ALLOWED_PROJECT_ROLES",
    "ALLOWED_WORKING_CONTEXT_FIELDS",
    "BUDGET_UNIT",
    "CODE_PROJECTION_BUDGET_INVALID",
    "CODE_PROJECTION_INPUT_INVALID",
    "DEFAULT_PROJECTION_BUDGET_BYTES",
    "DENIED_WORKING_CONTEXT_FIELDS",
    "LIMITATIONS",
    "MAX_FIELD_CHARS",
    "OMISSION_REASONS",
    "OMIT_BUDGET_EXHAUSTED",
    "OMIT_DUPLICATE_PART",
    "OMIT_POLICY_EXCLUDED",
    "OMIT_SENSITIVE_CONTENT",
    "OMIT_SOURCE_EMPTY",
    "OMIT_SOURCE_KIND_MISMATCH",
    "OMIT_SOURCE_REF_UNSUPPORTED",
    "PLACEHOLDER",
    "PROJECTION_API_VERSION",
    "PROJECT_CONTEXT_EXTERNAL_V1",
    "REDACTIONS",
    "REDACTION_PATH",
    "REQUIRED_PROJECT_KINDS",
    "RESIDUAL_LIMITATIONS",
    "SOURCE_CAPS",
    "CloudContextPart",
    "CloudContextProjection",
    "ContextProjector",
    "HostRedactionEnvironment",
    "ProjectionBudget",
    "ProjectionBudgetInvalid",
    "ProjectionError",
    "ProjectionInputInvalid",
    "ProjectionOmission",
    "Sanitized",
    "encoded_length",
    "sanitize",
]
