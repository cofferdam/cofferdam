"""The bounded development worker adapter.

A second Claude Code integration, and deliberately not a mode of the first. The
``claude-code`` adapter has no Bash and reaches a whole project root; this one
has a bounded shell and reaches nothing but an isolated worktree inside a mount
namespace. Neither is a configuration of the other, and a project must list this
adapter by id before anything can run under it.
"""

from __future__ import annotations

from . import cli
from .adapter import (
    ADAPTER_ID,
    DESCRIPTION,
    DISPLAY_NAME,
    PROMPT_SEPARATOR,
    ClaudeCodeWorkerAdapter,
    build_worker_payload,
    delivered_prompt,
)

__all__ = [
    "ADAPTER_ID",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "PROMPT_SEPARATOR",
    "ClaudeCodeWorkerAdapter",
    "build_worker_payload",
    "cli",
    "delivered_prompt",
]
