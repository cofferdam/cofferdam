"""Running a project's own checks, in a sandbox that holds no credential.

The finding this module exists because of
-----------------------------------------

PR1e's first design gave the worker a Bash allowlist — ``git``, ``python3``,
``pytest``, ``make`` — inside the same namespace that carried the Claude
subscription credential, and described those commands as bounded. **They are
not.** ``python3``, ``pytest`` and ``make`` are arbitrary-code launchers: a
prefix allowlist decides *which program starts*, and says nothing about what that
program then does.

An adversarial test against the shipped design confirmed it, with a fake
sentinel credential in the exact location the real one occupies:

* a shell in the namespace read the credential file directly;
* ``python3 -c`` — an allowed prefix — read it;
* sandboxed project code opened a socket and **sent the sentinel to a local
  listener**, which logged it.

So the credential was reachable by, and exfiltratable by, anything the model
chose to run. A command-prefix allowlist is an *intent filter*, not a boundary.

What replaces it
----------------

Project code no longer runs in the Claude namespace at all. It runs here: a
second ``bubblewrap`` sandbox with a deliberately different shape.

============================  ==========================  =====================
                              Claude phase                 check phase (here)
============================  ==========================  =====================
provider credential           present (it must sign in)    **absent**
network                       shared (it must reach the    **disabled**
                              API)
what runs                     the CLI, file tools only     project code
who chose the command         nobody — no Bash             host-owned policy
============================  ==========================  =====================

The two capabilities that were fused are now separated. The Claude control
process may authenticate. Project code may execute. Neither gets the other's
privilege, and no prompt instruction is load-bearing for that.

The command is not model text
-----------------------------

:data:`CHECK_COMMANDS` is a closed, code-owned table. A project selects a check
by **id** — the same discipline ``task-projects.json`` uses for roots — and an
unknown id is a refusal rather than a shell string. There is no parameter here
through which a model, a caller, a ``Makefile`` or a ``CLAUDE.md`` can contribute
an argument.

That last point matters more than it looks. ``make`` is deliberately **not** in
the table: its recipe lives in the repository, so choosing ``make`` would be
choosing to run whatever the project's own files say — which is model-writable
text in a worktree the model just edited.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

#: How long any one check may run.
CHECK_TIMEOUT_SECONDS = 600.0

#: How much output is kept. A check that produced more than this has said
#: everything useful long before the end.
MAX_CHECK_OUTPUT = 20000

#: The checks a project may ask for, by id.
#:
#: Closed and code-owned. Each value is a literal argument vector — not a string
#: to be split, not a template, not a format. Adding one is a source change
#: somebody reviews, which is the entire point.
#:
#: ``make`` is absent deliberately. Its recipe is a file in the repository, so
#: running it would run project-authored text — from a worktree a model has just
#: written to. The same reasoning excludes ``npm test`` (``package.json``
#: ``scripts`` is repository text) and any ``pytest`` plugin autoloading.
#:
#: ``unittest`` is the default and the only one guaranteed present: it is in the
#: standard library, so it works in a namespace with nothing installed. A check
#: that needs a package the sandbox does not have is a check that cannot run, and
#: it is better for that to be a truthful failure than a reason to widen the
#: sandbox.
CHECK_COMMANDS: Dict[str, Tuple[str, ...]] = {
    "python-unittest": ("python3", "-m", "unittest", "discover", "-v"),
    "python-unittest-quiet": ("python3", "-m", "unittest", "discover"),
    "python-compileall": ("python3", "-m", "compileall", "-q", "."),
    "none": (),
}

DEFAULT_CHECK_ID = "python-unittest-quiet"

#: The check sandbox's environment. No ``HOME`` pointing at anything with
#: credentials in it, no token, nothing inherited.
#:
#: ``PYTHONDONTWRITEBYTECODE`` keeps ``__pycache__`` out of a worktree whose diff
#: Cofferdam is about to observe; a check that dirties the tree it is measuring
#: would make the evidence worse.
CHECK_ENVIRONMENT: Dict[str, str] = {
    "HOME": "/tmp",
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
    "NO_COLOR": "1",
    "TERM": "dumb",
}

INTERIOR_WORKTREE = "/work"


class CheckUnavailable(Exception):
    """The check could not be run in a credential-free sandbox, so it was not run.

    Fails closed. Running the check *somewhere else* — in the Claude namespace,
    or uncontained — is precisely the shortcut this module exists to remove.
    """


@dataclass(frozen=True)
class CheckResult:
    """What the project's own checks did, observed by Cofferdam running them.

    This is an **observation**, not a claim, and the distinction is the reason
    the check phase is host-owned at all. A worker saying "tests passed" is
    something a worker said. This is something Cofferdam ran, in a sandbox the
    worker could not influence, with a command the worker did not choose.
    """

    check: str
    ran: bool
    exit_code: Optional[int]
    output: str
    failure: Optional[str] = None

    @property
    def exit_zero(self) -> bool:
        """The process exited 0. **Not** "the criteria are met".

        Named for what it measures rather than what somebody might want it to
        mean. M2K owns the vocabulary of acceptance — ``passed``, ``verdict``,
        ``all_met`` — and this is not that: it is one command's exit status,
        observed beside a dispatch, and PR1f is where it can become part of a
        judgement. A boolean called ``passed`` here would be read as acceptance
        by the first person to skim it.
        """
        return self.ran and self.exit_code == 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "check": self.check,
            "ran": self.ran,
            "exit_zero": self.exit_zero,
            "exit_code": self.exit_code,
            "output": self.output[:MAX_CHECK_OUTPUT],
            "failure": self.failure,
            # Named in the payload so a reader never has to remember which side
            # of the claim/observation line this sits on.
            "observed_by": "cofferdam",
        }


def resolve_check(check: Optional[str]) -> Tuple[str, Tuple[str, ...]]:
    """Turn a project's check id into an argument vector, or refuse.

    A lookup in a table this module owns. An unknown id is a refusal, never an
    attempt to interpret it as a command.
    """
    wanted = check or DEFAULT_CHECK_ID
    if wanted not in CHECK_COMMANDS:
        raise CheckUnavailable("unknown check id: " + str(wanted))
    return wanted, CHECK_COMMANDS[wanted]


def build_plan(*, worktree: Path, command: Tuple[str, ...]) -> Tuple[str, ...]:
    """The contained argument vector for one check. **No network, no credential.**

    Three differences from the Claude sandbox, each deliberate:

    ``--unshare-net``
        The check gets no network at all. Project code has no legitimate need to
        reach the internet during a bounded check, and the exfiltration test that
        motivated this module went out over exactly such a socket.
    no credential bind
        There is no worker home here. ``HOME`` is the private tmpfs, so
        ``~/.claude/.credentials.json`` does not exist to be read.
    no CLI bind
        The Claude binary is not mounted, so nothing here can start an agent.
    """
    executable = shutil.which("bwrap")
    if executable is None:
        raise CheckUnavailable(
            "bubblewrap is not installed, so checks cannot be run credential-free"
        )
    worktree = Path(worktree).resolve()
    if not worktree.is_dir():
        raise CheckUnavailable("the authorized worktree is not a directory")

    argv = [
        executable,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        # The line that makes exfiltration impossible rather than discouraged.
        "--unshare-net",
        "--die-with-parent",
        "--new-session",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/sbin", "/sbin",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", str(worktree), INTERIOR_WORKTREE,
        "--chdir", INTERIOR_WORKTREE,
    ]
    for name, value in sorted(CHECK_ENVIRONMENT.items()):
        argv += ["--setenv", name, value]
    argv += list(command)
    _assert_credential_free(tuple(argv))
    return tuple(argv)


def _assert_credential_free(argv: Tuple[str, ...]) -> None:
    """Check the built vector before it runs, on every run and not only in tests.

    Two properties, both cheap: nothing is bound except the worktree, and the
    network is unshared. A regression in either is the whole vulnerability
    coming back.
    """
    bound = [
        argv[index + 1]
        for index, token in enumerate(argv)
        if token in ("--bind", "--dev-bind") and index + 1 < len(argv)
    ]
    if len(bound) != 1:
        raise CheckUnavailable(
            "a check sandbox binds exactly one directory: " + ", ".join(bound)
        )
    if "--unshare-net" not in argv:
        raise CheckUnavailable("a check sandbox must have no network")
    for token in argv:
        if ".claude" in token or "credentials" in token:
            raise CheckUnavailable("a check sandbox never carries credential paths")


def run(*, worktree: Path, check: Optional[str] = None) -> CheckResult:
    """Run one project check, contained and credential-free.

    Returns a truthful result in every case. A check that could not run is
    ``ran=False`` with a reason — never a silent pass, because "we could not
    check" and "it passed" are the two things this layer must never confuse.
    """
    resolved, command = resolve_check(check)
    if not command:
        return CheckResult(
            check=resolved, ran=False, exit_code=None, output="",
            failure="this project runs no automated check",
        )

    argv = build_plan(worktree=worktree, command=command)
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
            # Nothing inherited. The launcher's own environment does not reach
            # the child; the child's is the `--setenv` list above.
            env={"PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check=resolved, ran=False, exit_code=None, output="",
            failure=f"the check did not finish in {int(CHECK_TIMEOUT_SECONDS)}s",
        )
    except OSError as exc:
        return CheckResult(
            check=resolved, ran=False, exit_code=None, output="",
            failure="the check could not be started: " + str(exc),
        )

    output = ((completed.stdout or "") + (completed.stderr or ""))[:MAX_CHECK_OUTPUT]
    return CheckResult(
        check=resolved,
        ran=True,
        exit_code=completed.returncode,
        output=output,
    )


def describe() -> Dict[str, object]:
    return {
        "mechanism": "bubblewrap",
        "network": "disabled",
        "credentials": "none mounted",
        "commands": sorted(CHECK_COMMANDS),
        "default": DEFAULT_CHECK_ID,
    }


__all__ = [
    "CHECK_COMMANDS",
    "CHECK_ENVIRONMENT",
    "CHECK_TIMEOUT_SECONDS",
    "DEFAULT_CHECK_ID",
    "INTERIOR_WORKTREE",
    "MAX_CHECK_OUTPUT",
    "CheckResult",
    "CheckUnavailable",
    "build_plan",
    "describe",
    "resolve_check",
    "run",
]
