"""The development worker's profile. A second file, and deliberately not a flag.

Why this is not a parameter on the existing adapter
---------------------------------------------------

``adapters/claude_code/cli.py`` says of its own tool list that Bash's absence is
"the single most important line in this package", because a Bash tool inside an
approved project root is still a general shell reachable by writing English into
a phone. That reasoning is correct and this module does not weaken it.

A development worker needs a shell — running the project's tests, reading Git
state and making a commit *are* the work. So the capability is not added to the
existing profile, where it would retroactively grant a shell to every task any
phone has ever been able to create. It lives in a separate profile, on a separate
adapter id, which a project must list explicitly before anything can use it.

Two projects can therefore differ: one permits ``claude-code`` and gets an agent
that cannot run a command; one permits ``claude-code-worker`` and gets a
development worker. Neither is a mode of the other, and there is no field
anywhere that turns the first into the second.

Why this profile has **no Bash**, after all
-------------------------------------------

The first version of this file granted Bash under a command-prefix allowlist —
``git``, ``python3``, ``pytest``, ``make`` — and called those bounded commands.
An adversarial test against that design, with a fake sentinel credential in the
exact location the real one occupies, showed they are nothing of the kind:

* a shell in the namespace read the credential file directly;
* ``python3 -c``, an *allowed* prefix, read it;
* sandboxed project code opened a socket and sent the sentinel to a local
  listener, which logged it.

``python3``, ``pytest`` and ``make`` are **arbitrary-code launchers**. A prefix
allowlist decides which program starts and says nothing about what that program
does once running — so the allowlist was an intent filter wearing the costume of
a boundary. The credential must be in this namespace for the CLI to sign in,
which means anything here that can run code can take it.

So project code does not run here any more. It runs in :mod:`...worker.checks` —
a second namespace with **no credential mounted and no network** — under a
command chosen from a host-owned table rather than by a model. The two
capabilities that were fused are separated: the Claude control process may
authenticate, project code may execute, neither gets the other's privilege.

Three layers, and what each is worth
------------------------------------

1. **No command tool.** No Bash means no process, no socket, no ``curl``. This is
   the layer that removes the *exfiltration path*, and it is the load-bearing one.
2. **File tools scoped to the worktree.** :data:`PROFILE_TOOLS` grants each file
   tool only over ``/work/**`` — a positive boundary, not a hole punched in a
   universal grant. :data:`DENIED_PATHS` additionally denies the worker home.
3. **Output scrubbing.** The final message is scrubbed before Cofferdam stores
   it, because a final message is the one channel that survives a namespace.

Only layer 1 is a guarantee about *capability*. Layer 2 is a permission rule and
layer 3 is a filter; neither is asked to carry the boundary alone.

Why the grant is positive, and what that changed
------------------------------------------------

The first version of this profile granted ``Read``/``Glob``/``Grep`` unscoped and
subtracted the worker home with :data:`DENIED_PATHS`. That is the "everywhere
except one known directory" shape, and an adversarial run showed what it costs:
with it in force the model read ``/etc/hostname`` and ``/usr/lib/os-release`` on
the first ask. Nothing there is a secret — the namespace is why — but the policy
was expressing *deny one place* when the intent was *allow one place*, and those
differ on every path nobody thought to name.

Scoping the grant to ``/work/**`` was verified rather than assumed. Under it,
worktree reads, writes, edits, globs and greps all still work, and paths outside
the worktree are refused: ``Claude requested permissions to read from
/etc/hostname, but you haven't granted it yet``.

Path authorization is by **resolved target**, and that was measured
-------------------------------------------------------------------

A repository is model-controlled input and may carry Git-tracked symlinks, so
whether the permission engine matches the *lexical* path or the *canonical* one
decides whether ``/work/anything`` can be an alias for the credential. This was
tested behaviourally against the installed CLI with a fake sentinel, never
assumed:

* ``/work/project-info.txt`` → ``/home/worker/.claude/...`` — **refused**;
* ``/work/a`` → ``/work/b`` → the same target (a chain) — **refused**;
* a *relative* escape, ``../home/worker/...`` — **refused**;
* ``/work/../home/worker/...`` and ``/work/./../home/worker/...`` — **refused**;
* ``/proc/self/root/home/worker/...`` and ``/proc/self/cwd/...`` — **refused**;
* and the discriminator that makes the rest mean something: ``/work/host-link``
  → ``/etc/hostname`` is *lexically inside* ``/work/**`` and was **still
  refused** under the scoped grant.

That last one is the proof. A lexical matcher would have allowed it. The engine
resolves the link and authorizes the target, so a symlink cannot launder a path
into scope — which is also why no worktree symlink preflight is shipped here:
the containment it would add already exists, and unnecessary sandbox code is its
own liability. Safe *internal* links keep working: ``docs.md`` → ``README.md``
and ``nested/up.md`` → ``../README.md`` both read normally.

The tool list is the runtime's, not this file's wish
-----------------------------------------------------

``--allowedTools`` is a *permission* allowlist and does not decide which tools
exist. The session's ``init`` event does, and reading it exposed a surface much
wider than this profile had accounted for: ``Artifact``, ``PushNotification``,
``RemoteTrigger``, ``CronCreate``/``CronDelete``/``CronList``, ``SendMessage``,
``Monitor``, ``DesignSync``, ``ToolSearch``, ``NotebookEdit``, ``EnterWorktree``
and the ``Task*`` family were all live in the worker's session while this profile
listed six tools.

Several of those are outward-facing — ``Artifact`` publishes a page, ``ToolSearch``
loads deferred tool schemas, ``NotebookEdit`` writes files outside the scoped
grant. So :data:`DENIED_TOOLS` now names the whole non-file surface, and the
result is checked against the runtime rather than reasoned about: the ``init``
event reports exactly ``["Edit", "Glob", "Grep", "Read", "Write"]``.

What is still absent
--------------------

No MCP, from anywhere. No settings file, so the profile in this file is the
profile that runs. No plugin, no agent definition, no ``--add-dir``, no resume.
The forbidden-flag list is asserted by test rather than trusted to review.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: The version this profile was written against, recorded rather than required.
VERIFIED_CLI_VERSION = "2.1.221"

EXECUTABLE_NAME = "claude"

SEARCH_DIRECTORIES: Tuple[str, ...] = (
    "~/.local/bin",
    "/usr/local/bin",
    "/usr/bin",
)

#: The model a development step runs on. Code-owned: there is no parameter
#: anywhere above this that selects a model, because "which model" is the first
#: field a caller learns to send and the second is "which tools".
PROFILE_MODEL = "sonnet"

#: The file tools a worker gets, and the only paths it gets them over.
#:
#: Names without paths, because the interior worktree is a sandbox constant that
#: :func:`build_interior_argv` pairs each of them with. Writing
#: ``Read(/work/**)`` here as well would be the same fact in two places, and the
#: copy is what drifts.
#:
#: **No ``Bash``**, and no other command tool. See the module docstring: a prefix
#: allowlist over interpreters is not a boundary, and the credential lives in
#: this namespace. Everything here is a file tool.
PROFILE_TOOLS: Tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
)

#: Everything the installed runtime offers that this worker must not have.
#:
#: Not a restatement of the allowlist. ``--allowedTools`` grants *permission*; it
#: does not remove a tool from the session, and the ``init`` event showed all of
#: these live in a worker session that had been granted six file tools. Naming
#: them here removes them, which the same event then confirms.
#:
#: Three groups, for three reasons:
#:
#: * command tools — an arbitrary-code launcher in the credential namespace;
#: * outward-facing tools — ``Artifact`` publishes, ``PushNotification`` and
#:   ``RemoteTrigger`` and ``CronCreate`` reach off the machine or outlive the
#:   dispatch, and controller network access must not become model network
#:   authority;
#: * file tools outside the scoped grant — ``NotebookEdit`` writes, and
#:   ``EnterWorktree`` moves the ground the scope is measured from.
DENIED_TOOLS: Tuple[str, ...] = (
    # Command execution and delegation.
    "Bash",
    "BashOutput",
    "KillShell",
    "Task",
    "Skill",
    "SlashCommand",
    # Network and anything that outlives or escapes this dispatch.
    "WebFetch",
    "WebSearch",
    "Artifact",
    "PushNotification",
    "RemoteTrigger",
    "CronCreate",
    "CronDelete",
    "CronList",
    "ScheduleWakeup",
    "Monitor",
    "DesignSync",
    "SendMessage",
    "SendUserFile",
    "ToolSearch",
    "ListMcpResources",
    "ReadMcpResource",
    "McpInput",
    # File and workspace tools outside the scoped grant.
    "NotebookEdit",
    "EnterWorktree",
    "ExitWorktree",
    # Session bookkeeping a bounded headless worker has no use for.
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "ReportFindings",
    "AskUserQuestion",
)

#: The worker home denied outright, on top of the scoped grant above.
#:
#: Defense in depth and nothing more. The positive ``/work/**`` grant is what
#: keeps the credential directory out of reach; this rule would keep it out of
#: reach if that grant were ever widened by mistake. Both were measured: the
#: scoped grant refuses these paths, and so does this rule, with the two
#: producing distinguishable messages.
#:
#: The doubled-slash variants are here because a permission pattern is matched
#: after normalization but written by hand, and ``//home`` costs nothing to name.
DENIED_PATHS: Tuple[str, ...] = (
    "Read(/home/worker/**)",
    "Glob(/home/worker/**)",
    "Grep(/home/worker/**)",
    "Write(/home/worker/**)",
    "Edit(/home/worker/**)",
    "Read(//home/worker/**)",
    "Glob(//home/worker/**)",
    "Grep(//home/worker/**)",
)

#: ``acceptEdits`` rather than ``bypassPermissions``.
#:
#: The stronger-sounding mode is the wrong choice even inside containment: it
#: would make the tool list above decorative, and there are no commands left for
#: it to bypass. Edits are accepted without a prompt because a headless run has
#: nobody to answer one.
PROFILE_PERMISSION_MODE = "acceptEdits"

#: Bounds. Larger than the CLI adapter's, because a development step legitimately
#: takes more turns than answering a question — and still finite, so a worker
#: that has not finished stops on its own rather than running until somebody
#: notices.
PROFILE_MAX_TURNS = 60
PROFILE_MAX_BUDGET_USD = "5.00"

#: How long one worker may run before Cofferdam stops waiting.
PROFILE_TIMEOUT_SECONDS = 1800.0

#: Flags that must never appear in a built argv, asserted by test.
FORBIDDEN_FLAGS: Tuple[str, ...] = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--bg",
    "--background",
    "--cloud",
    "--worktree",
    "--tmux",
    "--ide",
    "--chrome",
    "--plugin-dir",
    "--plugin-url",
    "--mcp-config",
    "--agents",
    "--add-dir",
    "--continue",
    "--resume",
    "--fork-session",
)

#: Git identity for anything the worker commits.
#:
#: Forced, and forced to something that reads as what it is. A commit a model
#: made must not be attributable to the operator: months later, ``git log`` is
#: the record of who did what, and a worker borrowing a person's name is the
#: quiet kind of wrong that is very hard to undo.
GIT_AUTHOR_NAME = "Cofferdam Worker"
GIT_AUTHOR_EMAIL = "worker@cofferdam.local"

GIT_ENVIRONMENT: Dict[str, str] = {
    "GIT_AUTHOR_NAME": GIT_AUTHOR_NAME,
    "GIT_AUTHOR_EMAIL": GIT_AUTHOR_EMAIL,
    "GIT_COMMITTER_NAME": GIT_AUTHOR_NAME,
    "GIT_COMMITTER_EMAIL": GIT_AUTHOR_EMAIL,
    # No pager, no editor. A worker that opened one would hang forever.
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "true",
}


def find_executable() -> Optional[Path]:
    """Locate the installed CLI, or return ``None``. Fixed order, fixed name."""
    for directory in SEARCH_DIRECTORIES:
        candidate = Path(os.path.expanduser(directory)) / EXECUTABLE_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(EXECUTABLE_NAME)
    if found:
        candidate = Path(found)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_cli_directory(executable: Path) -> Path:
    """The directory to mount read-only into the namespace.

    Resolved through its symlink here, unlike the CLI adapter's launch path, and
    for a different reason: this value becomes a **bind mount source**, and a
    bind of a symlink is a bind of wherever it pointed at mount time. Knowing
    exactly what is being exposed matters more here than surviving a
    ``claude update`` mid-run — and the resolution happens per dispatch, so an
    update between dispatches is picked up anyway.
    """
    return Path(os.path.realpath(executable))


def scoped_tools(interior_worktree: str) -> Tuple[str, ...]:
    """:data:`PROFILE_TOOLS`, each bound to the worktree it may operate on.

    ``Read`` becomes ``Read(/work/**)``. The scope comes from the sandbox's
    interior constant rather than from a literal here, so a worktree that ever
    appeared somewhere else could not leave a grant pointing at the old place.
    """
    scope = interior_worktree.rstrip("/")
    return tuple(f"{tool}({scope}/**)" for tool in PROFILE_TOOLS)


def build_interior_argv(*, interior_cli: str, interior_worktree: str) -> List[str]:
    """The CLI command line as it exists *inside* the namespace.

    Every element is a constant of this module or an interior path constant of
    the sandbox. The prompt is not an argument: it goes on stdin, exactly as the
    planner's does, so no part of it can be read as a flag.

    ``--add-dir`` is absent and its absence is load-bearing: the worker's
    reachable filesystem is decided by the mount namespace, and a flag that
    could widen it from inside would make the outer boundary advisory. The
    granted tools are scoped to ``interior_worktree`` for the same reason one
    layer up — a grant that names no path is a grant over everything mounted.
    """
    return [
        interior_cli,
        "-p",
        "--model",
        PROFILE_MODEL,
        "--output-format",
        "json",
        "--permission-mode",
        PROFILE_PERMISSION_MODE,
        "--allowedTools",
        *scoped_tools(interior_worktree),
        "--disallowedTools",
        *DENIED_TOOLS,
        *DENIED_PATHS,
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns",
        str(PROFILE_MAX_TURNS),
        "--max-budget-usd",
        PROFILE_MAX_BUDGET_USD,
    ]


__all__ = [
    "DENIED_PATHS",
    "DENIED_TOOLS",
    "EXECUTABLE_NAME",
    "FORBIDDEN_FLAGS",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_NAME",
    "GIT_ENVIRONMENT",
    "PROFILE_MAX_BUDGET_USD",
    "PROFILE_MAX_TURNS",
    "PROFILE_MODEL",
    "PROFILE_PERMISSION_MODE",
    "PROFILE_TIMEOUT_SECONDS",
    "PROFILE_TOOLS",
    "VERIFIED_CLI_VERSION",
    "build_interior_argv",
    "find_executable",
    "resolve_cli_directory",
    "scoped_tools",
]
