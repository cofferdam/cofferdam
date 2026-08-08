"""The process the systemd template actually starts.

``ExecStart`` points here with one argument — a project id — and this module
turns that into a running ``claude remote-control`` in the right directory, or
refuses and exits non-zero with a sentence somebody can read in ``journalctl``.

Why ``execv`` and not a subprocess
-----------------------------------

This program replaces itself with Claude rather than supervising it. That is the
correct shape for a ``Type=simple`` unit and it removes a whole class of bug:
there is no Python shim left holding a pipe, so systemd's ``ExecStop``, its stop
timeout and its ``KillMode`` act directly on the real process, ``SIGTERM``
reaches Claude without a forwarding hop, and the exit status systemd records is
Claude's own. A shim would have to reimplement signal forwarding and would get
the exit code subtly wrong; ``execv`` gets both for free.

It also means this package contains no ``subprocess`` call at all, which is why
the repository's "subprocess lives only in adapter code" structural test needs
no exemption for it.

The argument, and the fact that there is only one
-------------------------------------------------

``%i`` from the unit — the project id, and nothing else. There is no argument
here that names a directory, an executable, a flag, a model, a permission mode
or an environment variable, and there is no pass-through of extra ``argv``. A
second argument is a usage error, not an option. That is what makes the unit
instance a *project identifier* rather than a command channel: the phone, the
API and the unit name can each choose which project, and none of them can choose
what runs.

What is deliberately not done
-----------------------------

No conversation file is read, copied, parsed or watched. No credential is read,
printed or passed — ``HOME`` is inherited from the unit and the CLI does its own
subscription login, exactly as it would if the user had typed the command
themselves. Nothing about the environment is logged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from ..config import load_config
from ..tasks.errors import ProjectDisabled, ProjectRootInvalid, ProjectUnknown
from ..tasks.projects import load_projects, verify_root
from . import claude
from .errors import (
    ExecutableMissing,
    RemoteControlError,
    RemoteControlNotEnabled,
    SessionProjectDisabled,
    SessionProjectUnknown,
    SessionRootInvalid,
)

#: Exit code for every refusal. Distinct from 0 so ``Restart=on-failure``
#: applies, and uniform because the *reason* belongs in the log line, not in a
#: code somebody has to look up.
EXIT_REFUSED = 2

#: Exit code for a usage error — wrong argument count. Separate from
#: :data:`EXIT_REFUSED` because it means the unit template is wrong, not that
#: the configuration is.
EXIT_USAGE = 64


def _log(message: str) -> None:
    """One bounded line to stdout, for journald.

    No path, no environment, no credential, no command line. The project id is
    safe to print — the registry grammar admits only lowercase letters, digits,
    dash and underscore, so it cannot forge a second log line with a newline.
    """
    sys.stdout.write("cofferdam-rc: " + message + "\n")
    sys.stdout.flush()


def resolve(project_id: str, *, config=None):
    """Registered, enabled, Remote-Control-enabled, with a usable root.

    Returns ``(project, executable)``. Every failure is a
    :class:`~.errors.RemoteControlError`, so the caller has one thing to catch
    and one place that decides what a person is told.

    The root is verified *here*, immediately before the exec, rather than
    trusted from load time — the same "check closest to the work" rule the task
    project registry applies, and for the same reason: a directory can be
    deleted or replaced by a symlink between service start and this moment.
    """
    configuration = config if config is not None else load_config()
    registry = load_projects(configuration)

    try:
        project = registry.get(project_id)
    except ProjectDisabled as exc:
        raise SessionProjectDisabled() from exc
    except ProjectUnknown as exc:
        raise SessionProjectUnknown() from exc

    if not project.remote_control_enabled:
        raise RemoteControlNotEnabled()

    try:
        verify_root(project.root)
    except ProjectRootInvalid as exc:
        raise SessionRootInvalid(exc.detail) from exc

    executable = claude.find_executable()
    if executable is None or not claude.verify_executable(executable):
        raise ExecutableMissing()

    return project, executable


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    config=None,
    chdir: Callable[[str], None] = os.chdir,
    exec_fn: Callable[[str, List[str]], None] = os.execv,
) -> int:
    """Resolve the project named by the single argument, then become Claude.

    ``chdir`` and ``exec_fn`` are injected so the tests can prove the working
    directory and the exact argv without ever starting a Remote Control host.
    Production uses the real ones, and on the real path this function does not
    return — :func:`os.execv` replaces the process image.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        _log("usage: python -m cofferdam.workstation.sessions.host <project-id>")
        return EXIT_USAGE

    project_id = arguments[0]

    try:
        project, executable = resolve(project_id, config=config)
    except RemoteControlError as exc:
        # The message is Cofferdam's, and the detail is a constant from
        # errors.py. Neither can carry a path or a value read from disk.
        _log("refused: " + exc.message + (" — " + exc.detail if exc.detail else ""))
        return EXIT_REFUSED

    command = claude.build_argv(executable, project.project_id)

    # The working directory is the registered root and nothing else. Remote
    # Control operates on the current directory, so this line is what binds a
    # session to a project — and it is set from the registry, never from an
    # argument.
    chdir(str(project.root))

    _log("starting the Remote Control host for project " + project.project_id)

    exec_fn(command[0], command)

    # Only reached if exec failed without raising, which should not happen.
    _log("refused: the Claude command could not be started")
    return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    sys.exit(main())
