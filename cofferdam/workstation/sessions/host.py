"""The process the systemd template actually starts.

``ExecStart`` points here with one argument — a project id — and this module
turns that into a running ``claude remote-control`` in the right directory, or
refuses and exits non-zero with a sentence somebody can read in ``journalctl``.

Why this supervises rather than ``execv``-ing
----------------------------------------------

PR1 replaced this process with Claude, which was right for a foundation that
only had to start something. Capturing the Remote Control session link means
*reading* what the child prints, and a process that has replaced itself has
nothing left to read with. So M2H PR2 supervises instead, and :mod:`.wrapper`
pays back what ``execv`` gave away for free — signals forwarded to the child's
whole process group, and the child's exit status returned unchanged.

The stream is attached for one purpose: finding the session URL. It is redacted
before anything is logged, only the link is stored, and nothing here parses the
child's output for meaning. Remote Control prints operational startup output on
this stream; conversation content lives in the session, which Cofferdam does not
read and has nowhere to put.

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

import datetime
import sys
from typing import Callable, Optional, Sequence

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
from .state import LinkStore, new_generation
from .wrapper import SupervisedHost

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

    Returns ``(project, executable, config)``. Every failure is a
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

    return project, executable, configuration


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    config=None,
    store: Optional[LinkStore] = None,
    supervise: Optional[Callable[..., int]] = None,
) -> int:
    """Resolve the project named by the single argument, then run Claude.

    ``store`` and ``supervise`` are injected so the tests can prove the argv,
    the working directory, the captured link and the state transitions without
    ever starting a Remote Control host.

    State lifetime is the load-bearing part. A generation is minted here, any
    link from a previous launch is cleared **before** the child starts, and the
    state is cleared again when it exits — so a stored URL only ever refers to a
    process that was alive when it was written.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        _log("usage: python -m cofferdam.workstation.sessions.host <project-id>")
        return EXIT_USAGE

    project_id = arguments[0]

    try:
        project, executable, configuration = resolve(project_id, config=config)
    except RemoteControlError as exc:
        # The message is Cofferdam's, and the detail is a constant from
        # errors.py. Neither can carry a path or a value read from disk.
        _log("refused: " + exc.message + (" — " + exc.detail if exc.detail else ""))
        return EXIT_REFUSED

    command = claude.build_argv(executable, project.project_id)
    links_store = store if store is not None else LinkStore(configuration)
    generation = new_generation()

    try:
        # Before, not after. A crash between clearing and starting leaves no
        # link, which is the safe direction; the reverse would leave a stale
        # capability URL readable for the length of a launch.
        links_store.clear(project.project_id)
        links_store.write(
            project.project_id, generation=generation, observed_at=_now()
        )
    except RemoteControlError as exc:
        _log("refused: " + exc.message)
        return EXIT_REFUSED

    def on_link(link: str) -> None:
        # The only place a captured URL is written. Never logged.
        links_store.write(
            project.project_id,
            generation=generation,
            link=link,
            discovered_at=_now(),
            observed_at=_now(),
        )

    def on_auth_required() -> None:
        links_store.write(
            project.project_id,
            generation=generation,
            auth_required=True,
            observed_at=_now(),
        )

    _log(
        "starting the Remote Control host for project "
        + project.project_id
        + " (generation "
        + generation
        + ")"
    )

    runner = supervise if supervise is not None else _supervise
    try:
        status = runner(
            command,
            cwd=str(project.root),
            on_link=on_link,
            on_auth_required=on_auth_required,
            log=_log,
        )
    finally:
        # Whatever happened, the link is no longer live. Clearing in `finally`
        # means a crash in the supervisor does not leave a usable URL behind.
        try:
            links_store.clear(project.project_id)
        except RemoteControlError:
            _log("the runtime state could not be cleared")

    _log("the Remote Control host exited with status " + str(status))
    return status


def _supervise(argv, *, cwd, on_link, on_auth_required, log) -> int:
    host = SupervisedHost(
        argv,
        cwd=cwd,
        on_link=on_link,
        on_auth_required=on_auth_required,
        log=log,
    )
    return host.run()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    sys.exit(main())
