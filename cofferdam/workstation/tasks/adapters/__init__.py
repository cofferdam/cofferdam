"""The adapter registry: a code-owned table, built once at start-up.

The registry is a dictionary in source. That is the security property, not an
implementation detail: there is no path from a request to an import, a module
name, a class name or a factory. A client sends an ``adapter_id``; this module
looks it up in a table it built itself; an id that is not in the table is a
refusal, not an attempt to find one.

Each adapter is registered **only** when the server was explicitly configured to
allow it. Not merged-in, not defaulted-on, not toggleable through the API — when
the flag is absent the object is never constructed, so there is nothing for a
request to reach even if it names the id correctly. That is true of the
deterministic validation adapter and it is equally true of Claude Code, which
launches real processes and must never appear because somebody forgot to turn it
off.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..errors import AdapterUnknown
from .claude_code import ADAPTER_ID as CLAUDE_CODE_ADAPTER_ID
from .claude_code import ClaudeCodeAdapter
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


def build_registry(
    *,
    enable_validation_adapter: bool = False,
    enable_claude_code_adapter: bool = False,
) -> AdapterRegistry:
    """The adapter table for this process.

    **Empty by default, and that is not an oversight.** Both parameters default
    to ``False``, both are set only from host-owned configuration — a command
    line flag, a key in ``config.json``, or an environment variable in the unit
    file — and neither is reachable from any request. A workstation that was
    installed and never configured has a fully working task system with nothing
    registered to run in it.

    The Claude Code adapter is the one that launches real processes and edits
    real files, so its default matters most. Turning it on is a decision
    somebody makes at the workstation, in a file they can read, and it is
    announced on every start rather than only when the flag was typed.
    """
    adapters: List[TaskAdapter] = []
    if enable_validation_adapter:
        adapters.append(ValidationTaskAdapter())
    if enable_claude_code_adapter:
        # Constructed with no argument that came from anywhere but source. The
        # executable is *found* by `cli.find_executable`; there is no parameter
        # here for a path, and nothing above this line has one to pass.
        adapters.append(ClaudeCodeAdapter())
    return AdapterRegistry(tuple(adapters))


__all__ = [
    "CLAUDE_CODE_ADAPTER_ID",
    "VALIDATION_ADAPTER_ID",
    "ClaudeCodeAdapter",
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
