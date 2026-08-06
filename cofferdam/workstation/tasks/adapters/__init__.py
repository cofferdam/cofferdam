"""The adapter registry: a code-owned table, built once at start-up.

The registry is a dictionary in source. That is the security property, not an
implementation detail: there is no path from a request to an import, a module
name, a class name or a factory. A client sends an ``adapter_id``; this module
looks it up in a table it built itself; an id that is not in the table is a
refusal, not an attempt to find one.

The validation adapter is registered **only** when the server was explicitly
configured to allow it. Not merged-in, not defaulted-on, not toggleable through
the API — when the flag is absent the object is never constructed, so there is
nothing for a request to reach even if it names the id correctly.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..errors import AdapterUnknown
from .protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from .validation import ADAPTER_ID as VALIDATION_ADAPTER_ID
from .validation import ValidationTaskAdapter


class AdapterRegistry:
    """Every adapter this build can run, and nothing else.

    Constructed once in :func:`~cofferdam.workstation.service.create_app` and
    read thereafter. It is not mutable through any route: there is no register
    endpoint, and there is no plan for one — an adapter is code, and adding code
    is a deployment.
    """

    def __init__(self, adapters: Tuple[TaskAdapter, ...] = ()) -> None:
        self._adapters: Dict[str, TaskAdapter] = {}
        for adapter in adapters:
            self._adapters[adapter.adapter_id] = adapter

    def __contains__(self, adapter_id: object) -> bool:
        return isinstance(adapter_id, str) and adapter_id in self._adapters

    def ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def get(self, adapter_id: object) -> TaskAdapter:
        """Resolve an id, or refuse. Never constructs and never imports."""
        if not isinstance(adapter_id, str) or adapter_id not in self._adapters:
            raise AdapterUnknown()
        adapter = self._adapters[adapter_id]
        if not adapter.available():
            raise AdapterUnknown(adapter.unavailable_reason() or "that adapter is not usable here")
        return adapter

    def find(self, adapter_id: object) -> Optional[TaskAdapter]:
        if not isinstance(adapter_id, str):
            return None
        return self._adapters.get(adapter_id)

    def describe(self) -> List[dict]:
        return [self._adapters[key].describe() for key in sorted(self._adapters)]


def build_registry(*, enable_validation_adapter: bool = False) -> AdapterRegistry:
    """The adapter table for this process.

    Empty by default, and that is the honest state of this milestone: Task Core
    ships with no adapter that does real work, because the adapter that will —
    Claude Code — is the next milestone. A workstation running the default
    configuration has a fully working task system with nothing to run in it,
    which is exactly what "foundation" means here.
    """
    adapters: List[TaskAdapter] = []
    if enable_validation_adapter:
        adapters.append(ValidationTaskAdapter())
    return AdapterRegistry(tuple(adapters))


__all__ = [
    "VALIDATION_ADAPTER_ID",
    "AdapterCapabilities",
    "AdapterEvent",
    "AdapterOutcome",
    "AdapterRefusal",
    "AdapterRegistry",
    "TaskAdapter",
    "TaskContext",
    "ValidationTaskAdapter",
    "build_registry",
]
