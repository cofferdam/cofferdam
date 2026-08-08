"""The complete vocabulary Cofferdam will speak to the Claude Agent SDK.

This module is the Agent SDK counterpart of ``claude_code/cli.py``, and it is
written to the same rule: **if somebody wants to know what Cofferdam can make the
agent do, the answer is this file and only this file** — not "it depends what the
phone sent".

There is no function here that accepts a caller-supplied tool list, permission
mode, model, effort level, system prompt, MCP configuration, hook, plugin, agent
definition, settings path, extra CLI argument, executable, or working directory.
The two values that vary at all are a project root the *server* resolved from its
own registry and a session id this process generated a moment earlier.

The profile
-----------

Five tools, and the absent one is the important one. **Bash is not in the set**,
for the same reason it is absent from the Claude Code adapter: a shell inside an
approved project root is still a general shell on the workstation, reachable by
writing English into a phone. Cofferdam runs its own fixed Git observations
instead.

``acceptEdits`` lets the agent write files inside the project root without an
interactive prompt nobody could answer in a headless run. It is not shell access
and, with Bash absent from :data:`PROFILE_TOOLS`, there is no path to one.

``bypassPermissions`` is a value of the SDK's ``permission_mode`` and it is
**forbidden here permanently**. It is listed by name in
:data:`FORBIDDEN_PERMISSION_MODES` and asserted by test rather than trusted to
review, so introducing it is a visible failure rather than a quiet one.

One verified difference from the CLI adapter
--------------------------------------------

The Claude Code adapter builds its child's environment by *selection*: it calls
``Popen`` itself and passes a thirteen-name allowlist, so a variable reaches the
child only when somebody added it here on purpose.

The Agent SDK does not offer that. Its transport builds the child environment as
``{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **options.env, ...}`` —
read from the published source, not assumed — so ``env`` is an **override map
layered over the daemon's own environment**, never a replacement for it. The
child therefore inherits what the daemon has.

That is stated rather than papered over, and it is bounded by three facts. The
daemon's environment is host-owned: it comes from the systemd unit and an
optional ``EnvironmentFile`` on the workstation, and nothing a client sends
reaches it. The overrides in :data:`ENVIRONMENT_FORCED` are applied last and win.
And Cofferdam's own secret-bearing variable names are explicitly blanked in
:data:`ENVIRONMENT_BLANKED` — a named, bounded mitigation for the one class of
value Cofferdam is responsible for.

What is deliberately *not* blanked is the ``ANTHROPIC_*`` family. Emptying those
would change how the agent authenticates, and Cofferdam has not verified what an
empty value does to the CLI's sign-in path; guessing there could break
authentication or silently move spending to a different account. Narrowing the
inherited environment properly needs a custom SDK ``Transport``, which is a real
piece of work and belongs to a later PR rather than to a foundation. It is
recorded as a limitation in ``docs/CLAUDE_AGENT_SDK_ADAPTER.md``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: The built-in tools the agent may have **at all**. Everything absent from this
#: tuple does not exist in the session.
#:
#: Identical to the Claude Code adapter's set, and that is the point: the two
#: adapters are two transports for one policy, and a difference between these
#: tuples would mean switching transport quietly changed what an agent could do.
PROFILE_TOOLS: Tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
)

#: Named again on the deny side. Redundant with :data:`PROFILE_TOOLS` by
#: construction — a tool absent from ``tools`` is not in the session — and worth
#: the redundancy: this is the list a reader checks first when asking "can it run
#: commands", and an answer that requires reasoning about the absence of an entry
#: is a worse answer than one that says no.
PROFILE_DISALLOWED_TOOLS: Tuple[str, ...] = (
    "Bash",
    "BashOutput",
    "KillShell",
    "Task",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

#: Auto-accept file edits; prompt for anything else. See the module docstring.
PROFILE_PERMISSION_MODE = "acceptEdits"

#: Permission modes that must never appear in a built option set, listed by name
#: so that adding one is a visible failure. ``bypassPermissions`` is the reason
#: this constant exists; the others are here because a mode that changes what a
#: tool call means should be a deliberate decision, not a default that drifted.
FORBIDDEN_PERMISSION_MODES: Tuple[str, ...] = (
    "bypassPermissions",
    "dontAsk",
    "auto",
)

#: Bounds. A task that has not finished within these has not earned more. The
#: same numbers the Claude Code adapter uses, for the same reason as the tool
#: set.
PROFILE_MAX_TURNS = 24
PROFILE_MAX_BUDGET_USD = 2.00

#: Option names a caller must never be able to influence, asserted by test.
#: Every one of them is either an execution channel (``cli_path``, ``cwd``,
#: ``extra_args``, ``settings``, ``add_dirs``, ``mcp_servers``, ``hooks``,
#: ``plugins``, ``agents``) or a policy channel (``permission_mode``, ``model``,
#: ``effort``, ``system_prompt``, ``tools``, ``allowed_tools``). They are fixed
#: in this file; there is no parameter anywhere above this line that carries one.
CALLER_FORBIDDEN_OPTIONS: Tuple[str, ...] = (
    "add_dirs",
    "agents",
    "allowed_tools",
    "cli_path",
    "cwd",
    "effort",
    "extra_args",
    "hooks",
    "mcp_servers",
    "model",
    "permission_mode",
    "permission_prompt_tool_name",
    "plugins",
    "settings",
    "setting_sources",
    "skills",
    "system_prompt",
    "tools",
)

#: The one entry of the list above that :func:`build_option_values` does accept
#: as a parameter, named here so the exception is explicit rather than a hole.
#:
#: ``cli_path`` is host-supplied: it comes from the Claude Code adapter's fixed
#: search — one program name, one directory order, no caller-supplied component
#: — and is passed down by the adapter, never by a request. Everything else in
#: :data:`CALLER_FORBIDDEN_OPTIONS` has no parameter at all.
HOST_SUPPLIED_OPTIONS: Tuple[str, ...] = ("cli_path",)

#: Environment overrides applied last, so they win over whatever the daemon
#: inherited. Deliberately four entries and no secrets: a value here ends up in a
#: child process's environment, and this tuple is the list a reviewer reads to
#: confirm nothing sensitive was put there.
ENVIRONMENT_FORCED: Dict[str, str] = {
    # Identifies the caller in the CLI's user agent. Documented SDK option.
    "CLAUDE_AGENT_SDK_CLIENT_APP": "cofferdam",
    "CLAUDE_CODE_ENTRYPOINT": "cofferdam-agent-sdk",
    # Colour control in a stream something parses would be noise at best.
    "NO_COLOR": "1",
    "PYTHONIOENCODING": "utf-8",
}

#: Cofferdam's own secret-bearing variable names, blanked in the child.
#:
#: The SDK merges ``env`` over the daemon's environment, so a name set here is
#: overridden rather than removed — an empty string, which for Cofferdam's own
#: names is unambiguous because nothing in the child reads them for any purpose.
#: This is the bounded half of the environment story; the unbounded half is in
#: the module docstring, stated rather than claimed away.
ENVIRONMENT_BLANKED: Tuple[str, ...] = (
    "COFFERDAM_TOKEN",
)


class OptionPolicyError(ValueError):
    """A built option set that violates this file's own rules.

    Raised by :func:`verify_option_values`, which runs on every build rather
    than only in tests. A guard that only exists in a test suite protects the
    test suite; this one protects the launch.
    """


def new_session_id() -> str:
    """A session identifier Cofferdam generates and can therefore verify.

    A UUID because the SDK's ``session_id`` option documents that requirement.
    Generated here, never accepted from anywhere: an id that arrived over the
    network would be a client naming which conversation to attach to.
    """
    return str(uuid.uuid4())


def build_environment(inherited: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The override map handed to the SDK as ``ClaudeAgentOptions.env``.

    Small on purpose. This is *not* the child's whole environment — see the
    module docstring — and writing it as though it were would be the kind of
    comfortable inaccuracy this project exists to avoid. What it does is force
    the four values Cofferdam cares about and blank its own secret names.

    ``inherited`` is accepted so a test can pass a mapping instead of touching
    the process environment; it is read only to decide whether a blanked name is
    worth including, never copied through.
    """
    source = dict(os.environ if inherited is None else inherited)
    overrides: Dict[str, str] = dict(ENVIRONMENT_FORCED)
    for name in ENVIRONMENT_BLANKED:
        if name in source:
            overrides[name] = ""
    return overrides


def build_option_values(
    *,
    project_root: Path,
    session_id: str,
    cli_path: Optional[Path] = None,
    inherited_environment: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Every option value this adapter will ever pass, as plain data.

    Returned as a dictionary rather than a ``ClaudeAgentOptions`` so the profile
    can be asserted on a machine where the SDK is not installed — which is the
    machine the stdlib-only CI job runs on, and the one where a test that
    silently skipped would be worth nothing.

    ``cli_path`` is the CLI *already installed on this workstation*, found by the
    Claude Code adapter's fixed search and passed in by the caller. The SDK would
    otherwise prefer the copy it bundles inside its own wheel, and Cofferdam
    prefers the host's for three reasons: it is the one whose sign-in the
    workstation manages, it is the one the Claude Code adapter has been running
    against, and it is re-verified immediately before every launch. ``None`` is
    accepted and means "let the SDK choose", which is what a host with no CLI
    installed gets — and such a host has an unavailable adapter anyway.
    """
    values: Dict[str, Any] = {
        # -- where ---------------------------------------------------------
        # A real, server-resolved directory. There is no parameter above this
        # line through which a request could supply one.
        "cwd": str(project_root),
        # -- what it may do ------------------------------------------------
        "tools": list(PROFILE_TOOLS),
        "disallowed_tools": list(PROFILE_DISALLOWED_TOOLS),
        "permission_mode": PROFILE_PERMISSION_MODE,
        # Empty, and not an oversight: `allowed_tools` is the *auto-approve*
        # list, and pre-approving a tool is exactly the decision this milestone
        # keeps on a human surface. `permission_mode` already covers edits.
        "allowed_tools": [],
        # -- what it may not reach -----------------------------------------
        # No MCP server configured anywhere on this machine is loaded. A task
        # started from a phone must not inherit tools somebody wired into their
        # editor last month.
        "mcp_servers": {},
        "strict_mcp_config": True,
        # SDK isolation mode: no user, project or local settings file is read,
        # so the profile in this file is the profile that runs. This is also why
        # the adapter never needs to write a settings file to constrain the
        # agent — it constrains it by not reading any.
        "setting_sources": [],
        "settings": None,
        # The project root is the only directory. No second root, ever.
        "add_dirs": [],
        # Nothing programmatic gets to add capability: no subagents, no plugins,
        # no skills, no caller-supplied hooks, no permission-prompt MCP tool.
        "agents": None,
        "plugins": [],
        "skills": [],
        "hooks": None,
        "permission_prompt_tool_name": None,
        # -- what it is ----------------------------------------------------
        # No system prompt of Cofferdam's own. The CLI's default is what the
        # Claude Code adapter runs with, and a prompt written here would be an
        # invisible behavioural difference between two transports for one policy.
        "system_prompt": None,
        # Both left to the CLI's own default. A model identifier from an API
        # caller is forbidden, and pinning one in source would age into a model
        # that no longer exists.
        "model": None,
        "fallback_model": None,
        "effort": None,
        # -- bounds ---------------------------------------------------------
        "max_turns": PROFILE_MAX_TURNS,
        "max_budget_usd": PROFILE_MAX_BUDGET_USD,
        # -- the session ----------------------------------------------------
        # Cofferdam chooses the identifier so it can verify the one the SDK
        # reports back. A mismatch is a launch failure, not a shrug.
        "session_id": session_id,
        # Nothing is resumed, continued or forked in this foundation. The seam
        # for same-session follow-up is the live client object the adapter
        # holds, not a session id read off disk — which is what keeps "this
        # milestone does not auto-resume" structural.
        "resume": None,
        "continue_conversation": False,
        "fork_session": False,
        # No transcript is mirrored anywhere. Cofferdam does not read Claude
        # conversation history and has nowhere to put it.
        "session_store": None,
        # Partial message events would multiply the stream by an order of
        # magnitude to tell a phone nothing it does not already know.
        "include_partial_messages": False,
        "include_hook_events": False,
        "enable_file_checkpointing": False,
        # No additional CLI arguments. This is the field that would turn the
        # whole profile back into a suggestion, so it is empty and asserted.
        "extra_args": {},
        "env": build_environment(inherited_environment),
    }
    if cli_path is not None:
        values["cli_path"] = str(cli_path)
    verify_option_values(values)
    return values


def verify_option_values(values: Dict[str, Any]) -> None:
    """Check a built option set against this file's own rules, before it is used.

    Six checks, each of which has a matching test. They run at build time rather
    than only under test because the value of a rule is proportional to when it
    is enforced: a launch that would have violated one is refused here, before a
    process exists.
    """
    mode = values.get("permission_mode")
    if mode in FORBIDDEN_PERMISSION_MODES:
        raise OptionPolicyError("permission mode " + str(mode) + " is never allowed")
    if mode != PROFILE_PERMISSION_MODE:
        raise OptionPolicyError("the permission mode is fixed in source")

    tools = values.get("tools")
    if not isinstance(tools, list) or tuple(tools) != PROFILE_TOOLS:
        raise OptionPolicyError("the tool set is fixed in source")
    for forbidden in PROFILE_DISALLOWED_TOOLS:
        if forbidden in tools:
            raise OptionPolicyError(forbidden + " is never in the tool set")
    if values.get("allowed_tools"):
        raise OptionPolicyError("no tool is auto-approved")

    if values.get("mcp_servers") or not values.get("strict_mcp_config"):
        raise OptionPolicyError("no MCP server is configured, and none is inherited")
    if values.get("setting_sources") != [] or values.get("settings") is not None:
        raise OptionPolicyError("no settings file is loaded")
    if values.get("add_dirs") or values.get("extra_args"):
        raise OptionPolicyError("no extra directory or CLI argument is passed")
    if values.get("hooks") or values.get("agents") or values.get("plugins"):
        raise OptionPolicyError("no hook, subagent or plugin is configured")

    environment = values.get("env")
    if not isinstance(environment, dict):
        raise OptionPolicyError("the environment override must be a mapping")
    for name, value in environment.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise OptionPolicyError("environment overrides must be strings")
        if name not in ENVIRONMENT_FORCED and name not in ENVIRONMENT_BLANKED:
            raise OptionPolicyError("unexpected environment override: " + name)


def build_options(sdk: Any, values: Dict[str, Any], *, can_use_tool: Any = None) -> Any:
    """Turn verified values into the SDK's own options object.

    The only function in this file that touches the SDK, and it re-verifies
    before constructing: a caller that built the mapping some other way still
    passes the same gate. ``can_use_tool`` is a callable and therefore cannot be
    part of the plain-data profile, so it is a separate parameter — and the only
    thing this adapter ever passes there is the code-owned handler in
    :mod:`.session`, which denies.
    """
    verify_option_values(values)
    if can_use_tool is not None:
        return sdk.ClaudeAgentOptions(**values, can_use_tool=can_use_tool)
    return sdk.ClaudeAgentOptions(**values)


__all__ = [
    "CALLER_FORBIDDEN_OPTIONS",
    "HOST_SUPPLIED_OPTIONS",
    "ENVIRONMENT_BLANKED",
    "ENVIRONMENT_FORCED",
    "FORBIDDEN_PERMISSION_MODES",
    "PROFILE_DISALLOWED_TOOLS",
    "PROFILE_MAX_BUDGET_USD",
    "PROFILE_MAX_TURNS",
    "PROFILE_PERMISSION_MODE",
    "PROFILE_TOOLS",
    "OptionPolicyError",
    "build_environment",
    "build_option_values",
    "build_options",
    "new_session_id",
    "verify_option_values",
]
