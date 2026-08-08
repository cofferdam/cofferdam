"""The one place the Claude Agent SDK is imported, and the only place it may be.

Cofferdam runs on a personal workstation that may never enable this adapter. So
the SDK is an **optional extra**, and "optional" has to mean something stronger
than "listed under a different key in ``pyproject.toml``": importing
``cofferdam.workstation`` must not import it, starting the daemon must not
import it, and running the whole test suite on a machine without it must not
import it.

That property is held by there being exactly one function that imports it —
:func:`load` — called from inside adapter methods rather than at module scope.
Everything else in this package imports *this* module, which is standard library
only. A test asserts the absence of a top-level import across the package, so
the property survives somebody adding a convenient ``from claude_agent_sdk
import ...`` at the top of a file later.

What was verified, and how
--------------------------

The names below were read from the official distribution — the ``claude-agent-sdk``
source archive and wheel published by Anthropic — and not from memory or from an
older example. The record:

* distribution ``claude-agent-sdk``, import package ``claude_agent_sdk``
* version ``0.2.134``, ``Requires-Python >=3.10``, MIT
* runtime dependencies ``anyio>=4``, ``sniffio>=1``, ``mcp>=1.23,<2``, and
  ``typing_extensions`` below 3.11
* the session API is :class:`ClaudeSDKClient` with ``connect``, ``query``,
  ``receive_messages``, ``interrupt`` and ``disconnect``
* configuration is the ``ClaudeAgentOptions`` dataclass
* message classes are ``AssistantMessage``, ``UserMessage``, ``SystemMessage``,
  ``ResultMessage`` and the ``Task*Message`` family; content blocks are
  ``TextBlock``, ``ThinkingBlock``, ``ToolUseBlock``, ``ToolResultBlock``
* the permission channel is the ``can_use_tool`` callback returning
  ``PermissionResultAllow`` or ``PermissionResultDeny``
* errors derive from ``ClaudeSDKError``: ``CLIConnectionError``,
  ``CLINotFoundError``, ``ProcessError``, ``CLIJSONDecodeError``

One fact about the package is worth knowing before enabling the extra: the
published wheel is about 91 MB, because it **bundles its own Claude Code CLI
binary** under ``claude_agent_sdk/_bundled/``. Cofferdam does not use it — the
adapter pins ``cli_path`` to the CLI already installed on the host, which is the
one whose sign-in the workstation manages and the one the Claude Code adapter has
been running against. See :mod:`.options`.

Version handling
----------------

:data:`VERIFIED_SDK_VERSION` is a record, not a requirement. Refusing to run
against a newer SDK would age badly and would turn a routine upgrade into an
outage; what this module does instead is *report* the installed version so a
capability description can say which one is actually there. The one thing that
is checked is that the attributes this adapter uses exist — a missing one is a
precise refusal rather than an ``AttributeError`` in the middle of a task.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Optional, Tuple

#: What a person installs.
DISTRIBUTION_NAME = "claude-agent-sdk"

#: What Python imports. Different from the distribution name, which is why both
#: are written down: getting these two confused is how an optional dependency
#: ends up with an error message naming something that does not exist.
IMPORT_NAME = "claude_agent_sdk"

#: The Cofferdam extra that installs it.
EXTRA_NAME = "agent-sdk"

#: The version this adapter was written and verified against.
VERIFIED_SDK_VERSION = "0.2.134"

#: The bundled CLI version that release carries. Recorded because the adapter
#: deliberately does **not** use it, and a future reader should be able to see
#: that the choice was made rather than overlooked.
VERIFIED_BUNDLED_CLI_VERSION = "2.1.226"

#: The lowest Python the SDK supports. Cofferdam itself supports 3.9, so the
#: extra carries an environment marker and this adapter is simply unavailable on
#: an interpreter the SDK cannot be installed on.
MINIMUM_PYTHON = (3, 10)

#: Every attribute this adapter touches on the SDK module. Checked once, at load,
#: so a version that moved or removed one produces a sentence naming it instead
#: of a failure somewhere inside a task.
REQUIRED_ATTRIBUTES: Tuple[str, ...] = (
    "ClaudeSDKClient",
    "ClaudeAgentOptions",
    "AssistantMessage",
    "UserMessage",
    "SystemMessage",
    "ResultMessage",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "PermissionResultDeny",
    "ClaudeSDKError",
)

#: What somebody is told when it is not installed. One sentence about what is
#: missing and one about how to fix it — and the exact command, because "install
#: the extra" is not actionable if you have to guess its name.
MISSING_MESSAGE = (
    "the Claude Agent SDK is not installed on this workstation. "
    'Install it with: pip install -e ".[' + EXTRA_NAME + ']"'
)


class AgentSdkUnavailable(RuntimeError):
    """The SDK could not be used, with a sentence saying precisely why.

    Three causes, deliberately distinguished. **Not installed** is a thing
    somebody can fix with one command. **Too old an interpreter** is a thing they
    cannot fix that way, and telling them to reinstall would waste their time.
    **Installed but missing an attribute** means an incompatible version, and
    naming the attribute is the difference between a bug report and a shrug.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class SdkModule:
    """The loaded SDK, as this adapter is willing to use it.

    A thin, named handle rather than the module itself, so that every use site
    reads as a fixed set of names this package verified — and so a test can hand
    over a double without monkey-patching an import.
    """

    module: Any
    version: Optional[str]

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - delegation
        return getattr(self.module, name)


def python_supports_sdk() -> bool:
    import sys

    return sys.version_info >= MINIMUM_PYTHON


def available() -> bool:
    """Whether the SDK could be loaded right now. Never raises.

    Used by the adapter's ``available()`` so an unusable adapter is described as
    unusable rather than offered and then refused when pressed.
    """
    try:
        load()
    except AgentSdkUnavailable:
        return False
    return True


def unavailable_reason() -> Optional[str]:
    try:
        load()
    except AgentSdkUnavailable as exc:
        return exc.message
    return None


def load() -> SdkModule:
    """Import the SDK, or refuse with a precise message.

    The import is here, inside a function, and it is the only one in Cofferdam.
    Calling this is what makes the dependency real; nothing calls it during
    ordinary use of the workstation, and nothing calls it at import time.
    """
    if not python_supports_sdk():
        import sys

        raise AgentSdkUnavailable(
            "the Claude Agent SDK needs Python "
            + ".".join(str(part) for part in MINIMUM_PYTHON)
            + " or newer; this workstation runs "
            + ".".join(str(part) for part in sys.version_info[:2])
        )
    try:
        module = importlib.import_module(IMPORT_NAME)
    except ImportError:
        # Deliberately not chained. The underlying ImportError names a module
        # path, and this message names the thing to install; showing both would
        # bury the actionable half.
        raise AgentSdkUnavailable(MISSING_MESSAGE) from None

    missing = [name for name in REQUIRED_ATTRIBUTES if not hasattr(module, name)]
    if missing:
        raise AgentSdkUnavailable(
            "the installed Claude Agent SDK does not provide "
            + missing[0]
            + ", so this version is not one Cofferdam can drive. Cofferdam was "
            "written against " + DISTRIBUTION_NAME + " " + VERIFIED_SDK_VERSION
        )
    return SdkModule(module=module, version=installed_version(module))


def installed_version(module: Any = None) -> Optional[str]:
    """The installed SDK version, bounded, or ``None``. Never raises.

    Read from the module's own ``__version__`` and falling back to distribution
    metadata, because both have been the authoritative one at different times in
    other packages and neither is worth depending on alone.
    """
    if module is not None:
        raw = getattr(module, "__version__", None)
        if isinstance(raw, str) and raw:
            return raw[:40]
    try:
        import importlib.metadata as metadata

        return str(metadata.version(DISTRIBUTION_NAME))[:40]
    except Exception:
        return None


__all__ = [
    "DISTRIBUTION_NAME",
    "EXTRA_NAME",
    "IMPORT_NAME",
    "MINIMUM_PYTHON",
    "MISSING_MESSAGE",
    "REQUIRED_ATTRIBUTES",
    "VERIFIED_BUNDLED_CLI_VERSION",
    "VERIFIED_SDK_VERSION",
    "AgentSdkUnavailable",
    "SdkModule",
    "available",
    "installed_version",
    "load",
    "python_supports_sdk",
    "unavailable_reason",
]
