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

The environment, and where it is actually decided
-------------------------------------------------

``ClaudeAgentOptions.env`` is an **override map layered over the calling
process's own environment**, never a replacement for it — the SDK's transport
builds ``{**os.environ, "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **options.env, …}``,
read from the published 0.2.134 source. M2I PR1 recorded that as an unresolved
limitation, because a child inheriting the daemon's environment inherits the
daemon's secrets.

It is no longer a limitation, and the fix is not in this file. Cofferdam runs the
SDK inside a helper process it launches itself, with a complete code-owned
environment built by selection — see :mod:`.hostenv`. Because that process's
``os.environ`` *is* the allowlist, the SDK's merge produces the allowlist. The
few entries in :data:`ENVIRONMENT_FORCED` below are what remains genuinely
useful to say through the SDK's own option, and they are exactly the values that
would otherwise depend on the helper's launcher getting them right twice.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: The tools that let the agent **act on the workstation**.
#:
#: Identical to the Claude Code adapter's complete set, and that is the point:
#: the two adapters are two transports for one policy, and a difference between
#: these tuples would mean switching transport quietly changed what an agent
#: could *do*. A test asserts they match.
PROFILE_ACTION_TOOLS: Tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
)

#: The tool that lets the agent **ask a person something**, and the one place the
#: two transports legitimately differ.
#:
#: It acts on nobody. It reads no file, writes no file, runs no command and
#: leaves the workstation untouched; its entire effect is to put a question in
#: front of somebody. That is why adding it does not weaken the parity rule
#: above — the parity rule is about what an agent can do to a machine, and this
#: does nothing to one.
#:
#: It is here because it is the reason M2I exists. The Claude Code adapter cannot
#: support it: a CLI stream gives no channel on which an answer could be
#: delivered mid-turn, so a question there is text in a transcript. The SDK's
#: permission callback is such a channel, and this tool is what uses it.
#:
#: **Its input schema is not verified.** See :mod:`.question`. Until a live spike
#: settles it, a question whose input this build cannot read conservatively
#: becomes bounded activity and never a fabricated clarification.
PROFILE_QUESTION_TOOLS: Tuple[str, ...] = ("AskUserQuestion",)

#: The built-in tools the agent may have **at all**. Everything absent from this
#: tuple does not exist in the session.
PROFILE_TOOLS: Tuple[str, ...] = PROFILE_ACTION_TOOLS + PROFILE_QUESTION_TOOLS

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

#: The profile's name, so that "which profile does Cofferdam ship" has a one-word
#: answer that can be asserted, published in a capability description, and
#: quoted in a pull request.
#:
#: There is exactly one, there is no second, and there is no parameter anywhere
#: that selects between them. A named profile is not a profile *system*: naming
#: it makes the single shipped set quotable, and the moment somebody adds a
#: second name they have to add the selector too, which is the visible change
#: this constant exists to force.
PROFILE_NAME = "cofferdam-project-edit-v1"

#: What the profile is for, in a sentence. Published, so a person deciding
#: whether to enable the adapter reads the intent rather than inferring it from a
#: tool list.
PROFILE_INTENDED_USE = (
    "Reading, searching and editing files inside one project directory the "
    "workstation's own registry named, driven from a phone."
)

#: What this profile is **not**, stated as facts a reader can check against the
#: values above rather than as reassurance.
#:
#: The last entry is the one that matters most and is the one a sandbox claim
#: would paper over. ``cwd`` plus an empty ``add_dirs`` is what the CLI is *told*;
#: it is a configuration boundary enforced by the agent's own tool
#: implementations, not a kernel one. Cofferdam has not verified that a
#: sufficiently determined ``Read`` of an absolute path outside the project root
#: is refused, and this build does not claim it is. What it does claim is
#: narrower and true: there is no shell in the session, so there is no general
#: execution primitive to escape *with*, and every path Cofferdam itself resolves
#: comes from the project registry.
PROFILE_LIMITATIONS: Tuple[str, ...] = (
    "No shell. Bash, BashOutput and KillShell are absent from the session and "
    "named again on the deny list.",
    "No network tool. WebFetch and WebSearch are absent.",
    "No MCP server, plugin, hook, subagent or skill is loaded, and no settings "
    "file is read.",
    "No caller may choose a tool, a permission mode, a model, an effort level, "
    "a budget or a directory. Every one of them is fixed in source.",
    "bypassPermissions is forbidden by name and refused at build time.",
    "The project root is a configuration boundary the CLI is asked to respect, "
    "not a kernel sandbox. Cofferdam has not verified that filesystem access "
    "outside the registered root is impossible, and does not claim it is.",
)

#: The filesystem boundary, named as the mechanism rather than as an outcome.
PROFILE_FILESYSTEM_BOUNDARY = (
    "One directory: the root the server resolved from its own project registry "
    "and re-verified on disk immediately before the launch. It is passed as the "
    "helper process's working directory and as the SDK's cwd, with add_dirs "
    "empty. No request field anywhere above this layer carries a path."
)

#: The environment policy, in one line, pointing at the module that holds it.
PROFILE_ENVIRONMENT_POLICY = (
    "A complete child environment built by allowlist in hostenv, passed to "
    "Popen, replacing rather than merging with the daemon's own. No credential "
    "of Cofferdam's or of any other provider is copied into it."
)

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

#: Environment overrides applied last, so they win over whatever the helper
#: process has. Deliberately four entries and no secrets: a value here ends up in
#: a child process's environment, and this mapping is the list a reviewer reads
#: to confirm nothing sensitive was put there.
#:
#: Every name is also in :data:`~.hostenv.CHILD_ENVIRONMENT_FORCED`, so the value
#: is already correct before the SDK is asked. The redundancy is deliberate:
#: these four are the ones whose being wrong would be silent — a mangled encoding
#: or an unattributed user agent — and stating them at both layers means neither
#: layer's mistake is the only thing standing between them and the child.
ENVIRONMENT_FORCED: Dict[str, str] = {
    # Identifies the caller in the CLI's user agent. Documented SDK option.
    "CLAUDE_AGENT_SDK_CLIENT_APP": "cofferdam",
    "CLAUDE_CODE_ENTRYPOINT": "cofferdam-agent-sdk",
    # Colour control in a stream something parses would be noise at best.
    "NO_COLOR": "1",
    "PYTHONIOENCODING": "utf-8",
}

#: Kept empty, and the emptiness is the change.
#:
#: PR1 blanked ``COFFERDAM_TOKEN`` here because the SDK's merge meant the child
#: would otherwise inherit it. Blanking was a denylist — it protected the one
#: name somebody thought of — and the helper-process boundary replaced it with an
#: allowlist, which protects every name nobody thought of. A name is now absent
#: from the child because :data:`~.hostenv.CHILD_ENVIRONMENT_ALLOWLIST` does not
#: contain it, not because this tuple remembered to erase it.
#:
#: The constant survives so that :func:`verify_option_values` still has a rule to
#: enforce and so a future reader can see the mechanism was replaced rather than
#: dropped.
ENVIRONMENT_BLANKED: Tuple[str, ...] = ()


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

    Small on purpose, and honest about what it is: an **override map**, not the
    child's environment. Writing it as though it were the environment would be
    the kind of comfortable inaccuracy this project exists to avoid — the child's
    environment is the helper process's own, built by :mod:`.hostenv` and passed
    to ``Popen``, and this only forces four values on top of it.

    ``inherited`` is accepted so a test can pass a mapping instead of touching
    the process environment. It is read to confirm that nothing needing a blank
    is present and is never copied through: no value from it appears in the
    result.
    """
    source = dict(os.environ if inherited is None else inherited)
    overrides: Dict[str, str] = dict(ENVIRONMENT_FORCED)
    for name in ENVIRONMENT_BLANKED:  # pragma: no cover - empty by design
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


def describe_profile() -> Dict[str, Any]:
    """The shipped profile as plain data, for a capability report and for tests.

    Everything here is read from the constants above rather than restated, so a
    profile that drifted from its own description is not a shape this function
    can produce. It is deliberately buildable on a machine with no SDK installed
    — the stdlib-only CI job is where a profile assertion is worth the most, and
    one that silently skipped there would be worth nothing.

    It carries no path, no environment value and no session identifier. What it
    describes is the policy, which is the same on every host; what varies per
    launch is a project root the server resolved, and that belongs to a task
    rather than to a profile.
    """
    return {
        "profile": PROFILE_NAME,
        "intended_use": PROFILE_INTENDED_USE,
        "tools": list(PROFILE_TOOLS),
        "action_tools": list(PROFILE_ACTION_TOOLS),
        "question_tools": list(PROFILE_QUESTION_TOOLS),
        "disallowed_tools": list(PROFILE_DISALLOWED_TOOLS),
        "permission_mode": PROFILE_PERMISSION_MODE,
        "forbidden_permission_modes": list(FORBIDDEN_PERMISSION_MODES),
        "auto_approved_tools": [],
        "max_turns": PROFILE_MAX_TURNS,
        "max_budget_usd": PROFILE_MAX_BUDGET_USD,
        "environment_policy": PROFILE_ENVIRONMENT_POLICY,
        "filesystem_boundary": PROFILE_FILESYSTEM_BOUNDARY,
        "caller_settable_options": [],
        "limitations": list(PROFILE_LIMITATIONS),
    }


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
    "PROFILE_ACTION_TOOLS",
    "PROFILE_DISALLOWED_TOOLS",
    "PROFILE_ENVIRONMENT_POLICY",
    "PROFILE_FILESYSTEM_BOUNDARY",
    "PROFILE_INTENDED_USE",
    "PROFILE_LIMITATIONS",
    "PROFILE_MAX_BUDGET_USD",
    "PROFILE_MAX_TURNS",
    "PROFILE_NAME",
    "PROFILE_PERMISSION_MODE",
    "PROFILE_QUESTION_TOOLS",
    "PROFILE_TOOLS",
    "OptionPolicyError",
    "build_environment",
    "build_option_values",
    "build_options",
    "describe_profile",
    "new_session_id",
    "verify_option_values",
]
