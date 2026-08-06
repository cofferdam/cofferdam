"""Agent Task Core: durable tasks, a strict lifecycle, and replaceable adapters.

The four layers this package keeps apart, and why the separation is the product
rather than an implementation preference:

1. **Task Core** — this package. Identity, lifecycle, persistence, events,
   policy, the API contract, cancellation and follow-up semantics. It knows
   nothing about Claude Code, Cursor, a CLI, a process or a model, and there is
   no name from any of those in it.
2. **Agent adapters** — :mod:`.adapters`. One object per integration, chosen
   from a code-owned table, declaring what it can actually do.
3. **Origin/return-route adapters** — the PWA today; a ChatGPT app, an Opera
   companion or a CLI later. An origin is where a task was asked for, assigned
   by the server from the authenticated request.
4. **Resource/evidence** — narrow here on purpose: an event may carry bounded
   references, each labelled with whether Cofferdam observed it or an adapter
   merely said so. The full audit is a later milestone.

Manual-first, and meant permanently
-----------------------------------

Nothing in this package routes, plans, decides or infers. A person chooses the
project, the adapter, the prompt, and every follow-up and cancellation. There is
no model call anywhere in Task Core, and no text produced by a model is ever
authority for an action on this machine.
"""

from __future__ import annotations

from .adapters import AdapterRegistry, TaskAdapter, build_registry
from .errors import TaskError
from .identity import new_task_id, valid_task_id
from .models import (
    STATES,
    TASK_API_VERSION,
    TERMINAL_STATES,
    TaskEvent,
    TaskSnapshot,
)
from .projects import ProjectRegistry, TaskProject, load_projects
from .service import TaskService
from .store import TaskStore

__all__ = [
    "STATES",
    "TASK_API_VERSION",
    "TERMINAL_STATES",
    "AdapterRegistry",
    "ProjectRegistry",
    "TaskAdapter",
    "TaskError",
    "TaskEvent",
    "TaskProject",
    "TaskService",
    "TaskSnapshot",
    "TaskStore",
    "build_registry",
    "load_projects",
    "new_task_id",
    "valid_task_id",
]
