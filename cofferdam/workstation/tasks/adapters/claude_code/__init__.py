"""Claude Code, contained.

Every line of Claude-specific knowledge in Cofferdam lives under this package —
the executable name, the argument template, the permission profile, the
stream-json frame shapes, the process handling, the Git probes. Task Core
imports none of it. The only thing outside this directory that knows Claude Code
exists is :func:`~..build_registry`, which constructs
:class:`~.adapter.ClaudeCodeAdapter` when the host enabled it, and the PWA, which
renders whatever the adapter's capabilities say.

That boundary is checked by a test rather than left to habit: nothing in
``cofferdam/workstation/tasks`` outside this package may import from it except
the registry.

Four modules, four jobs:

:mod:`.cli`
    The installed CLI as Cofferdam is willing to invoke it. Fixed executable,
    fixed argv, one permission profile, environment allowlist, auth probe.
:mod:`.frames`
    The bounded parser. Untrusted newline-delimited JSON in, a closed set of
    normalized records out.
:mod:`.process`
    One process, identified by pid *and* start time *and* process group *and*
    run id, signalled only after all four still agree.
:mod:`.evidence`
    The fixed Git observations that turn a claim into something Cofferdam saw.
"""

from __future__ import annotations

from .adapter import (
    ADAPTER_ID,
    DEFAULT_MAX_CONCURRENT_TASKS,
    DESCRIPTION,
    DISPLAY_NAME,
    LIMITATIONS,
    ClaudeCodeAdapter,
)

__all__ = [
    "ADAPTER_ID",
    "DEFAULT_MAX_CONCURRENT_TASKS",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "LIMITATIONS",
    "ClaudeCodeAdapter",
]
