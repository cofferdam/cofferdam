"""The Claude Agent SDK adapter (Lane B), off unless a host turns it on.

Five modules, and the split is the design:

:mod:`.sdk`
    The optional-dependency boundary. The only place in Cofferdam that imports
    ``claude_agent_sdk``, and it does so inside a function.
:mod:`.options`
    The complete, code-owned provider configuration. No caller reaches it.
:mod:`.normalize`
    SDK messages into provider-neutral events. Imports no SDK, so the part with
    the interesting behaviour is testable without the dependency.
:mod:`.session`
    One task, one session, on one thread, behind a synchronous boundary.
:mod:`.adapter`
    The Task Core adapter. Owns no lifecycle state.

Importing this package is safe on a workstation with no SDK installed: nothing
here imports it at module scope, and the adapter simply describes itself as
unavailable. The only thing that constructs the adapter is
:func:`~..build_registry`, and only when the host passed the flag.

This adapter does **not** replace the Claude Code adapter. Both can be
registered; each is opt-in; the Claude Code one remains the validated path and
the default production choice. The retirement rule is in ``ROADMAP.md``: the CLI
adapter goes only after verified parity with what PR #21 validated live.
"""

from __future__ import annotations

from .adapter import (
    ADAPTER_ID,
    DEFAULT_MAX_CONCURRENT_TASKS,
    DESCRIPTION,
    DISPLAY_NAME,
    LIMITATIONS,
    ClaudeAgentSdkAdapter,
)
from .sdk import AgentSdkUnavailable

__all__ = [
    "ADAPTER_ID",
    "DEFAULT_MAX_CONCURRENT_TASKS",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "LIMITATIONS",
    "AgentSdkUnavailable",
    "ClaudeAgentSdkAdapter",
]
