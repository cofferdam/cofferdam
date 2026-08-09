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

Two Claude transports, and one table
------------------------------------

M2I added a second Claude adapter, and this module is the only place that knows
both exist. That is deliberate on two counts.

Each has its **own** switch: enabling the Agent SDK adapter neither disables nor
replaces the CLI one, and a project still has to list an adapter before a task
may use it. The CLI adapter is the one validated live from a phone against a real
host, and ``ROADMAP.md`` retires it only after verified parity.

And each stays **independent of the other in source**: this file resolves the
host's installed CLI and hands the path to the SDK adapter, rather than the SDK
adapter's package importing the CLI adapter's. A registry is allowed to name
adapters — a table with no names in it is not a table — while an adapter that
imported a sibling would be stranded the day that sibling is removed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..errors import AdapterUnknown
from .claude_agent_sdk import ADAPTER_ID as CLAUDE_AGENT_SDK_ADAPTER_ID
from .claude_agent_sdk import ClaudeAgentSdkAdapter
from .claude_code import ADAPTER_ID as CLAUDE_CODE_ADAPTER_ID
from .claude_code import ClaudeCodeAdapter
from .claude_code import cli as claude_code_cli
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


class DuplicateAdapterId(ValueError):
    """Two adapters claimed one id, which is a build error rather than a state.

    Raised at construction, so a mistake in :func:`build_registry` or in a new
    adapter's class attribute stops the service from starting instead of
    producing a registry whose contents depend on list order.
    """

    def __init__(self, adapter_id: str) -> None:
        super().__init__("two adapters are registered as '" + adapter_id + "'")
        self.adapter_id = adapter_id


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
            if adapter.adapter_id in self._adapters:
                # Loudly, at construction, rather than by the second one winning.
                # Two adapters answering to one id is a build that cannot say
                # which program a task ran — and with a second Claude provider
                # now in the tree, a copied ``adapter_id`` is a realistic
                # mistake rather than a theoretical one. A silent overwrite
                # would produce a workstation whose behaviour depended on the
                # order of a list.
                raise DuplicateAdapterId(adapter.adapter_id)
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

    def shutdown(self) -> None:
        """Ask every registered adapter to release what it owns. Never raises.

        Called once from the daemon's lifespan as the service stops. Before this
        existed, ``ClaudeAgentSdkAdapter.shutdown`` was implemented and had no
        caller: a daemon stopped with a live task left its helper process to
        notice on its own. It does notice — the helper's loop ends when its stdin
        closes — but "the child works out that its parent is gone" is a weaker
        guarantee than "the parent closed it", and only one of the two is
        bounded.

        Each adapter is asked separately and a failure in one does not stop the
        next, because the cost of that coupling is somebody else's process
        surviving the shutdown.
        """
        for adapter_id in sorted(self._adapters):
            try:
                self._adapters[adapter_id].shutdown()
            except Exception:  # pragma: no cover - tidying must never raise
                continue


def build_registry(
    *,
    enable_validation_adapter: bool = False,
    enable_claude_code_adapter: bool = False,
    enable_claude_agent_sdk_adapter: bool = False,
) -> AdapterRegistry:
    """The adapter table for this process.

    **Empty by default, and that is not an oversight.** Every parameter defaults
    to ``False``, all are set only from host-owned configuration — a command line
    flag, a key in ``config.json``, or an environment variable in the unit file —
    and none is reachable from any request. A workstation that was installed and
    never configured has a fully working task system with nothing registered to
    run in it.

    The Claude Code adapter is the one that launches real processes and edits
    real files, so its default matters most. Turning it on is a decision
    somebody makes at the workstation, in a file they can read, and it is
    announced on every start rather than only when the flag was typed.

    The Agent SDK adapter is a **separate** switch, and enabling it does not
    disable or replace anything. Both Claude adapters can be registered at once;
    they have different ids, and a project still has to list an adapter before a
    task may use it. That is deliberate: the CLI adapter is the one validated
    live from a phone against a real host, it stays the fallback, and the
    roadmap's retirement rule says it goes only after verified parity.
    """
    adapters: List[TaskAdapter] = []
    if enable_validation_adapter:
        adapters.append(ValidationTaskAdapter())
    if enable_claude_code_adapter:
        # Constructed with no argument that came from anywhere but source. The
        # executable is *found* by `cli.find_executable`; there is no parameter
        # here for a path, and nothing above this line has one to pass.
        adapters.append(ClaudeCodeAdapter())
    if enable_claude_agent_sdk_adapter:
        # Same rule. The SDK is not imported by constructing this — the adapter
        # only reports itself unavailable if it is missing — so registering it on
        # a host without the extra installed produces a truthful description
        # rather than an import error at start-up.
        #
        # The CLI path is resolved *here* rather than inside the SDK adapter,
        # and the wiring is deliberate. The SDK would otherwise prefer the copy
        # of Claude Code it bundles inside its own wheel; Cofferdam prefers the
        # one installed on the host, because that is the binary whose sign-in
        # the workstation manages and the one the CLI adapter already drives.
        # Resolving it in the SDK adapter's own package would make that package
        # import the other adapter — a dependency that would strand it the day
        # the CLI adapter is retired, and a boundary Task Core asserts. This
        # module is the layer allowed to know both adapters exist.
        adapters.append(
            ClaudeAgentSdkAdapter(cli_path=claude_code_cli.find_executable())
        )
    return AdapterRegistry(tuple(adapters))


__all__ = [
    "CLAUDE_AGENT_SDK_ADAPTER_ID",
    "CLAUDE_CODE_ADAPTER_ID",
    "VALIDATION_ADAPTER_ID",
    "ClaudeAgentSdkAdapter",
    "ClaudeCodeAdapter",
    "AdapterCapabilities",
    "AdapterEvent",
    "AdapterOutcome",
    "AdapterRefusal",
    "AdapterRegistry",
    "DuplicateAdapterId",
    "TaskAdapter",
    "TaskContext",
    "ValidationTaskAdapter",
    "build_registry",
]
