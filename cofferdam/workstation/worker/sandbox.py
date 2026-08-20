"""The host boundary a development worker actually runs behind.

Why this exists rather than a sentence in a prompt
--------------------------------------------------

The ``claude-code`` adapter keeps a coding model safe by removing Bash. A
*development* worker cannot work that way — running tests, reading Git state and
making a commit are the job — so the boundary has to move from "which tools
exist" to "what the process can reach". Those are different guarantees, and only
the second one survives a model that decides to look around.

A prompt saying *stay in your directory* is not that guarantee. With a shell and
no containment, every other project on this machine, the canonical Cofferdam
state, the A/B slots and the operator's home directory are one ``cd`` away. So
the containment is a real one: ``bubblewrap``, unprivileged, with a mount
namespace that simply does not contain those paths.

Verified on this host, not assumed
----------------------------------

The plan below was checked against the installed CLI (2.1.221) before it was
written down. Inside it: the subscription login authenticates, DNS resolves, and
``/home`` **does not exist** — so ``/home/nrgis/cofferdam`` is not "denied", it
is absent. The difference matters: a denial can be misconfigured, and a path
that was never mounted cannot be.

The synthetic home, and why the real one is not bound
-----------------------------------------------------

The CLI needs a home directory to authenticate. Binding the operator's would
hand the worker ``~/.claude.json`` — which on this workstation records eight
project paths including the production slots — plus every other dotfile. So the
worker gets a *constructed* home containing exactly two things: a read-only bind
of the credential file, and a fresh empty CLI state file it may write.

That is the same distinction the CLI adapter draws when it passes ``HOME`` and
nothing else: **Cofferdam grants reachability, never possession.** Here it is
narrower still, because reachability itself is scoped to one file.

What this module is not
-----------------------

Not a general sandboxing platform, and not a security product. It builds one
argument vector for one purpose from values it derives itself. There is no
profile selector, no policy file, no caller-supplied bind and no way to add one
from a request — the parameters are a worktree path, a home directory and an
executable, and every one of them is produced by code above it.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: The containment program. One name, not configurable, for the reason the CLI
#: adapter gives about its own executable: a configurable program name is an
#: execution field with extra steps.
BWRAP_EXECUTABLE = "bwrap"

#: Where the worker's authorized worktree appears *inside* the namespace.
#:
#: A fixed interior path, deliberately. The worker is told to work in ``/work``
#: and that is true wherever the host actually keeps the directory, so the host's
#: layout never has to appear in a prompt, in a log line, or in anything a client
#: might later be shown.
INTERIOR_WORKTREE = "/work"
INTERIOR_HOME = "/home/worker"
INTERIOR_CLI = "/opt/claude-cli"

#: Read-only system directories the process needs to be a working Unix process.
#: Everything not in this list is absent from the namespace rather than denied.
SYSTEM_READONLY: Tuple[str, ...] = ("/usr", "/etc")

#: Resolver state. Ubuntu's ``/etc/resolv.conf`` is a symlink into ``/run``, so
#: binding ``/etc`` alone produces a namespace with working sockets and no DNS —
#: which presents as a worker that hangs rather than one that fails.
RESOLVER_PATHS: Tuple[str, ...] = ("/run/systemd/resolve",)

#: Interior symlinks for the merged-``/usr`` layout.
USR_SYMLINKS: Tuple[Tuple[str, str], ...] = (
    ("usr/lib", "/lib"),
    ("usr/lib64", "/lib64"),
    ("usr/bin", "/bin"),
    ("usr/sbin", "/sbin"),
)

#: Paths that must never be bound into a worker namespace, asserted by test
#: rather than trusted to review. Named individually so that adding a bind that
#: happened to expose one is a failing test and not a discovery.
NEVER_BIND: Tuple[str, ...] = (
    "/home/nrgis/cofferdam/repo",
    "/home/nrgis/cofferdam/slots",
    "/home/nrgis/cofferdam/state",
    "/home/nrgis/cofferdam/secrets",
    "/home/nrgis/.ssh",
    "/home/nrgis/.claude.json",
    "/root",
    "/var/lib",
)

#: The environment the contained process gets. Built by selection, never by
#: copy-and-delete, for the reason the CLI adapter's allowlist gives.
ENVIRONMENT: Dict[str, str] = {
    "HOME": INTERIOR_HOME,
    "PATH": "/usr/bin:/bin",
    "USER": "worker",
    "LOGNAME": "worker",
    "SHELL": "/bin/sh",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "dumb",
    "NO_COLOR": "1",
    "PYTHONIOENCODING": "utf-8",
    "CLAUDE_CODE_ENTRYPOINT": "cofferdam-worker",
    "TMPDIR": "/tmp",
}


class SandboxUnavailable(Exception):
    """This host cannot contain a development worker, so none is started.

    Fails closed and says why. The alternative — running the worker uncontained
    because the containment was missing — would mean the isolation guarantee
    quietly became a comment, which is worse than not having the feature.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


def find_bwrap() -> Optional[Path]:
    found = shutil.which(BWRAP_EXECUTABLE)
    return Path(found) if found else None


def available() -> Tuple[bool, Optional[str]]:
    """Whether a worker can be contained here, and the reason when it cannot."""
    executable = find_bwrap()
    if executable is None:
        return False, "bubblewrap is not installed, so a worker cannot be contained"
    for required in SYSTEM_READONLY:
        if not Path(required).is_dir():
            return False, f"{required} is missing, so no usable namespace can be built"
    return True, None


@dataclass(frozen=True)
class SandboxPlan:
    """One contained execution, fully decided before anything runs.

    Frozen and inspectable on purpose: the argument vector is the security
    boundary, so it is a value a test can assert about rather than a side effect
    of a function that already launched something.
    """

    worktree: Path
    home: Path
    cli_directory: Path
    argv: Tuple[str, ...]
    environment: Dict[str, str]

    @property
    def interior_worktree(self) -> str:
        return INTERIOR_WORKTREE

    def bound_host_paths(self) -> Tuple[str, ...]:
        """Every host path this plan makes visible. What a test checks.

        Read back out of the built ``argv`` rather than from a list kept
        alongside it, because a second list would be the thing that drifts from
        what actually runs.
        """
        found: List[str] = []
        flags = {"--bind", "--ro-bind", "--dev-bind"}
        for index, token in enumerate(self.argv):
            if token in flags and index + 1 < len(self.argv):
                found.append(self.argv[index + 1])
        return tuple(found)


def build_home(directory: Path, *, credentials: Optional[Path] = None) -> Path:
    """Construct the worker's synthetic home. Copies one file, and no more.

    The credential file is **copied** rather than bound read-only, and that is a
    deliberate trade. The CLI refreshes its own token, so a read-only bind
    produces a worker that fails partway through a long run when the refresh is
    denied. A copy into a 0600 file inside a per-dispatch directory keeps the
    blast radius to one dispatch and keeps the operator's own credential file
    from being written by a model's process.

    Nothing else from the real home is reproduced. In particular there is no
    ``.claude.json`` copy: the worker gets an empty one, so it cannot read which
    other projects exist on this machine.
    """
    home = Path(directory)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    state = home / ".claude.json"
    if not state.exists():
        state.write_text("{}\n", encoding="utf-8")
        os.chmod(state, 0o600)

    source = credentials if credentials is not None else default_credentials()
    if source is not None and source.is_file():
        target = home / ".claude" / ".credentials.json"
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
    try:
        os.chmod(home, 0o700)
    except OSError:  # pragma: no cover - odd filesystems
        pass
    return home


def default_credentials() -> Optional[Path]:
    """The operator's CLI credential file, if it exists.

    Located rather than configured, and read only to be copied. Cofferdam does
    not parse it, does not know its format, and stores nothing from it.
    """
    candidate = Path(os.path.expanduser("~/.claude/.credentials.json"))
    return candidate if candidate.is_file() else None


def build_plan(
    *,
    worktree: Path,
    home: Path,
    cli_directory: Path,
    command: Tuple[str, ...],
) -> SandboxPlan:
    """The complete contained command line, every element decided here.

    ``command`` is the interior argument vector — built by the worker adapter's
    own code-owned profile module, never by a caller — and it is appended after
    the containment arguments so nothing in it can add a bind.

    Three properties this construction has, which a test asserts:

    * the only writable host paths are the worktree and the synthetic home;
    * no path in :data:`NEVER_BIND` appears anywhere in the vector;
    * ``/home`` as the host knows it is not bound at all, so the operator's
      directory and every other project are absent rather than protected.
    """
    executable = find_bwrap()
    if executable is None:
        raise SandboxUnavailable(
            "bubblewrap is not installed, so a worker cannot be contained"
        )

    worktree = Path(worktree).resolve()
    home = Path(home).resolve()
    cli_directory = Path(cli_directory).resolve()
    if not worktree.is_dir():
        raise SandboxUnavailable("the authorized worktree is not a directory")
    if not home.is_dir():
        raise SandboxUnavailable("the worker home was not prepared")

    argv: List[str] = [
        str(executable),
        # One namespace per worker. `--unshare-net` is deliberately absent: the
        # worker talks to the Anthropic API and to a Git remote, and a worker
        # that cannot reach either is not a development worker.
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        # The child dies with the daemon. Without it, a killed dispatch could
        # leave a model's process running with write access to a worktree.
        "--die-with-parent",
        # Its own session, so it cannot reach the daemon's controlling terminal.
        "--new-session",
    ]
    for path in SYSTEM_READONLY:
        argv += ["--ro-bind", path, path]
    for path in RESOLVER_PATHS:
        if Path(path).exists():
            argv += ["--ro-bind", path, path]
    for target, link in USR_SYMLINKS:
        argv += ["--symlink", target, link]
    argv += [
        "--proc", "/proc",
        "--dev", "/dev",
        # Private, in-memory, and gone when the process is. A worker's scratch
        # files do not outlive it and never touch the host's /tmp.
        "--tmpfs", "/tmp",
        "--ro-bind", str(cli_directory), INTERIOR_CLI,
        "--bind", str(home), INTERIOR_HOME,
        "--bind", str(worktree), INTERIOR_WORKTREE,
        "--chdir", INTERIOR_WORKTREE,
    ]
    for name, value in sorted(ENVIRONMENT.items()):
        argv += ["--setenv", name, value]
    argv += list(command)

    plan = SandboxPlan(
        worktree=worktree,
        home=home,
        cli_directory=cli_directory,
        argv=tuple(argv),
        environment=dict(ENVIRONMENT),
    )
    _assert_contained(plan)
    return plan


def _assert_contained(plan: SandboxPlan) -> None:
    """Check the built vector against the invariants, before it is ever run.

    Cheap, and it runs on every launch rather than only in tests. A containment
    bug that only a test catches is a containment bug that ships the day
    somebody runs the code by another path.
    """
    bound = plan.bound_host_paths()
    for forbidden in NEVER_BIND:
        for path in bound:
            if path == forbidden or path.startswith(forbidden.rstrip("/") + "/"):
                raise SandboxUnavailable(
                    "the containment plan would expose a path it must not",
                    detail=forbidden,
                )
    writable = {
        plan.argv[index + 1]
        for index, token in enumerate(plan.argv)
        if token == "--bind" and index + 1 < len(plan.argv)
    }
    if writable != {str(plan.home), str(plan.worktree)}:
        raise SandboxUnavailable(
            "a worker may write only in its worktree and its own home",
            detail=", ".join(sorted(writable)),
        )


def describe() -> Dict[str, object]:
    """What this host can promise about worker containment. For the read model."""
    ok, reason = available()
    return {
        "mechanism": "bubblewrap" if ok else None,
        "available": ok,
        "unavailable_reason": reason,
        "network": "shared",
        "writable": ["authorized worktree", "per-dispatch worker home"],
    }


__all__ = [
    "BWRAP_EXECUTABLE",
    "ENVIRONMENT",
    "INTERIOR_CLI",
    "INTERIOR_HOME",
    "INTERIOR_WORKTREE",
    "NEVER_BIND",
    "RESOLVER_PATHS",
    "SYSTEM_READONLY",
    "SandboxPlan",
    "SandboxUnavailable",
    "available",
    "build_home",
    "build_plan",
    "default_credentials",
    "describe",
    "find_bwrap",
]
