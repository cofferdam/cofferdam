"""The installed Claude Code CLI, as Cofferdam is willing to invoke it.

This module holds the *entire* vocabulary Cofferdam will ever speak to Claude
Code: one executable name, one argument template, one permission profile, one
authentication probe. Nothing here is parameterised by anything a client sent,
and there is no function in this file that takes a caller-supplied flag, tool
name, path, environment mapping or permission mode.

That is the point. If somebody wants to know what Cofferdam can make the CLI do,
the answer is this file and only this file — not "it depends what the phone
sent".

The version this was built against
----------------------------------

``2.1.221``, verified on the workstation. See ``docs/CLAUDE_CODE_ADAPTER.md``
for the discovery record, including the two probes whose results chose the
architecture. :data:`VERIFIED_CLI_VERSION` is not a requirement — refusing to
run against a newer CLI would age badly — it is what the parser and the profile
were written to, recorded so a future reader knows what "verified" meant.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: The version whose ``--help`` and stream frames this adapter was written to.
VERIFIED_CLI_VERSION = "2.1.221"

#: The only program name this adapter will ever look for. Not configurable: a
#: configurable executable name is an executable field with extra steps, and the
#: whole safety argument of this milestone is that no such field exists.
EXECUTABLE_NAME = "claude"

#: Where to look, in order, before falling back to ``PATH``. The npm-style
#: user-local install is where the workstation's own CLI lives.
SEARCH_DIRECTORIES: Tuple[str, ...] = (
    "~/.local/bin",
    "/usr/local/bin",
    "/usr/bin",
)

# -- the permission profile --------------------------------------------------
#
# One profile, code-owned, with fixed choices. There is no second profile and no
# way to select one, because "which profile" would be the first field a client
# learned to send.

#: The built-in tools Claude may have **at all**. Everything absent from this
#: tuple does not exist in the session — probe 2 confirmed that asking for a
#: shell command with Bash outside the set produces "no Bash tool is available"
#: rather than an approval prompt or an execution.
#:
#: Bash is absent deliberately, and it is the single most important line in this
#: package. A Bash tool inside an approved project root is still a general shell
#: on the workstation, reachable by writing English into a phone. Cofferdam runs
#: its own fixed Git observations instead, and those live in :mod:`.evidence`.
PROFILE_TOOLS: Tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
)

#: ``acceptEdits`` lets Claude write files inside the project root without an
#: interactive prompt that nobody could answer in a headless run. It does *not*
#: grant shell access: the documentation is explicit that other commands and
#: network requests still need an allow rule, and with Bash removed from
#: :data:`PROFILE_TOOLS` there is no path to one anyway.
PROFILE_PERMISSION_MODE = "acceptEdits"

#: Bounds. A task that has not finished within these has not earned more.
PROFILE_MAX_TURNS = 24
PROFILE_MAX_BUDGET_USD = "2.00"

#: Flags that must never appear in a built argv, asserted by test rather than
#: trusted to review. Listed by name so that adding one is a visible failure.
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

# -- the environment allowlist -----------------------------------------------

#: The only environment variables passed to the child, and the reason each one
#: is here.
#:
#: ``HOME`` is the load-bearing entry: the installed CLI authenticates with a
#: ``claude.ai`` subscription login whose credentials live under the user's home
#: directory, and without it the child cannot authenticate. Cofferdam does not
#: read those credentials, does not know their format, and does not name their
#: path — it passes ``HOME`` and lets the CLI do its own thing. That is the
#: whole boundary: Cofferdam grants *reachability*, never *possession*.
#:
#: Everything else is here to make the child a working Unix process with correct
#: Unicode handling. No token, no key, no ``ANTHROPIC_*`` variable, and nothing
#: read from a request.
ENVIRONMENT_ALLOWLIST: Tuple[str, ...] = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
)

#: Forced into the child regardless of what the daemon inherited. UTF-8 is not
#: negotiable: a Turkish prompt that survives the API and the database only to
#: be mangled by a child process locale would be a data-loss bug wearing an
#: encoding costume.
ENVIRONMENT_FORCED: Dict[str, str] = {
    "PYTHONIOENCODING": "utf-8",
    "CLAUDE_CODE_ENTRYPOINT": "cofferdam",
    # Colour and cursor control in a stream we parse would be noise at best.
    "NO_COLOR": "1",
    "TERM": "dumb",
}


def find_executable() -> Optional[Path]:
    """Locate the installed CLI, or return ``None``.

    Fixed search order, fixed program name, no caller-supplied component.

    The path is **not** resolved through its symlink, and that is a deliberate
    reversal of the usual instinct. ``~/.local/bin/claude`` is a link into a
    versioned directory, and ``claude update`` moves the link and may remove the
    old version — so a daemon that pinned the resolved path at start-up would
    keep pointing at a binary that no longer exists, and every task would fail
    after an unrelated update with a message about nothing.

    Resolving would also buy no safety worth that cost. The link lives in the
    home directory of the same user the daemon runs as; anybody who can rewrite
    it can already run anything as that user, so pinning defends against an
    attacker who has, by then, no need for it. What does matter is re-checking
    the path immediately before launch, which is :func:`verify_executable` —
    the same "check closest to the work" rule the project registry uses for
    roots.
    """
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


def verify_executable(executable: Path) -> bool:
    """Whether that path is still a runnable program, right now.

    Called immediately before every launch. A CLI that was uninstalled or
    half-replaced between service start and a task should produce a task that
    says so, not a process that fails in a way nobody can read.
    """
    try:
        return executable.is_file() and os.access(executable, os.X_OK)
    except OSError:
        return False


def build_argv(executable: Path, session_id: str) -> List[str]:
    """The complete command line, every element of it decided in this file.

    The prompt is **not** an argument and there is no parameter here that could
    make it one — the only caller-supplied value is a session id this process
    generated itself moments earlier. Everything else is a constant.

    Why each flag is present:

    ``-p``
        Non-interactive. Without it the CLI opens a session nobody can drive.
    ``--input-format stream-json``
        The reason a follow-up can reach the same session. One process, many
        user turns, delivered as JSON on stdin.
    ``--output-format stream-json`` with ``--verbose``
        Incremental structured events. ``--verbose`` is required for the full
        event stream rather than only a final frame.
    ``--session-id``
        Cofferdam chooses the session identifier so it can *verify* the one the
        CLI reports back. A mismatch is a launch failure, not a shrug.
    ``--permission-mode`` / ``--tools``
        The profile. See :data:`PROFILE_TOOLS`.
    ``--strict-mcp-config`` with no ``--mcp-config``
        Every MCP server configured anywhere on this machine is ignored. A task
        started from a phone must not inherit tools somebody wired into their
        editor last month.
    ``--setting-sources ""``
        No user, project or local settings file is loaded, so the permission
        profile in this file is the profile that runs. This is also why the
        adapter never needs to write a settings file to constrain Claude — it
        constrains it by not reading any.
    ``--disable-slash-commands``
        A prompt beginning ``/something`` is text, not a command invocation.
    ``--no-session-persistence``
        The session lives and dies with the process. Nothing is left on disk for
        a later ``--resume`` to reattach to, which is what makes "this milestone
        does not auto-resume" structural rather than a promise.
    ``--max-turns`` / ``--max-budget-usd``
        Bounded work. A runaway agent stops on its own.
    """
    return [
        str(executable),
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
        session_id,
        "--permission-mode",
        PROFILE_PERMISSION_MODE,
        "--tools",
        ",".join(PROFILE_TOOLS),
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


def build_environment(inherited: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The child's complete environment: allowlisted, then forced.

    Built by *selection*, never by copy-and-delete. A denylist would ship every
    variable somebody adds to the unit file next year; this ships thirteen names
    that are in source, and a new variable in the daemon's environment reaches
    the child only when somebody adds it here on purpose.
    """
    source = dict(os.environ if inherited is None else inherited)
    child = {
        name: source[name]
        for name in ENVIRONMENT_ALLOWLIST
        if name in source and isinstance(source[name], str)
    }
    child.update(ENVIRONMENT_FORCED)
    return child


# -- authentication ----------------------------------------------------------

#: How long the auth probe may take. It is a local read; if it has not answered
#: in this long, something is wrong and saying so beats hanging a task.
AUTH_PROBE_TIMEOUT_SECONDS = 20.0


class AuthStatus:
    """The answer to one question: can the CLI authenticate right now?

    Deliberately not a record of *who*. The probe's JSON carries an email
    address, an organisation id and an organisation name; none of them is read
    into an attribute here, so there is no field for a later refactor to
    accidentally log, store or publish. What survives the constructor is a
    boolean and a bounded non-identifying method name.
    """

    __slots__ = ("logged_in", "method", "probe_failed")

    def __init__(
        self,
        *,
        logged_in: bool,
        method: Optional[str] = None,
        probe_failed: bool = False,
    ) -> None:
        self.logged_in = logged_in
        # Bounded and non-identifying: "claude.ai" or "apiKey", not an account.
        self.method = method if isinstance(method, str) and len(method) <= 40 else None
        self.probe_failed = probe_failed


#: Values of ``authMethod`` the probe may report. Anything else is discarded
#: rather than passed through, because an unrecognised value from a program's
#: output is not something to render into a person's screen unexamined.
KNOWN_AUTH_METHODS = frozenset({"claude.ai", "apiKey", "bedrock", "vertex", "foundry"})


def probe_authentication(
    executable: Path,
    *,
    environment: Optional[Dict[str, str]] = None,
    runner=None,
) -> AuthStatus:
    """Ask the CLI whether it is authenticated, using its documented command.

    ``claude auth status --json``. A fixed invocation with no caller-supplied
    element, run without a shell, with the same allowlisted environment as a
    task. It reads nothing from disk itself: Cofferdam never opens a credential
    file, a keychain, a browser profile or a token store, and this function is
    the only thing in the package that asks about authentication at all.

    Only two fields of the response are read — ``loggedIn`` and ``authMethod``.
    The email address and organisation identifiers in the same document are
    passed over without being assigned to anything.
    """
    argv = [str(executable), "auth", "status", "--json"]
    env = build_environment() if environment is None else dict(environment)
    call = runner if runner is not None else _run_probe
    try:
        completed = call(argv, env)
    except Exception:
        # A probe that could not run is not a claim that the user is logged out.
        # Saying "you are signed out" because a subprocess failed would send
        # somebody to re-authenticate an account that was fine.
        return AuthStatus(logged_in=False, probe_failed=True)

    code, out = completed
    if code != 0 or not out:
        return AuthStatus(logged_in=False, probe_failed=code != 0)
    try:
        document = json.loads(out[:20000])
    except ValueError:
        return AuthStatus(logged_in=False, probe_failed=True)
    if not isinstance(document, dict):
        return AuthStatus(logged_in=False, probe_failed=True)

    method = document.get("authMethod")
    return AuthStatus(
        logged_in=document.get("loggedIn") is True,
        method=method if method in KNOWN_AUTH_METHODS else None,
    )


def _run_probe(argv: Sequence[str], env: Dict[str, str]) -> Tuple[int, str]:
    """Run the fixed probe. No shell, no cwd from anywhere, bounded output."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False, no user input
        list(argv),
        shell=False,
        capture_output=True,
        timeout=AUTH_PROBE_TIMEOUT_SECONDS,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, (completed.stdout or "")[:20000]


def probe_version(executable: Path, *, runner=None) -> Optional[str]:
    """The installed version string, bounded, for the capability report."""
    call = runner if runner is not None else _run_probe
    try:
        code, out = call([str(executable), "--version"], build_environment())
    except Exception:
        return None
    if code != 0:
        return None
    first = (out or "").strip().splitlines()[:1]
    return first[0][:80] if first else None


__all__ = [
    "AUTH_PROBE_TIMEOUT_SECONDS",
    "ENVIRONMENT_ALLOWLIST",
    "ENVIRONMENT_FORCED",
    "EXECUTABLE_NAME",
    "FORBIDDEN_FLAGS",
    "PROFILE_MAX_BUDGET_USD",
    "PROFILE_MAX_TURNS",
    "PROFILE_PERMISSION_MODE",
    "PROFILE_TOOLS",
    "VERIFIED_CLI_VERSION",
    "AuthStatus",
    "build_argv",
    "build_environment",
    "find_executable",
    "verify_executable",
    "probe_authentication",
    "probe_version",
]
