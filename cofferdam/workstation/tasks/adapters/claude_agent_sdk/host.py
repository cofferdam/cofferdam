"""The helper process: the only place in Cofferdam where the Agent SDK runs.

Started by :mod:`.hostclient` with a fixed argv, a complete code-owned
environment, and the project root as its working directory. It speaks the bounded
protocol in :mod:`.hostproto` on stdin and stdout, drives one
:class:`~.session.SdkSession`, and exits when that session ends.

Why this process exists
-----------------------

Because ``ClaudeAgentOptions.env`` is an override map layered over
``os.environ`` — verified in the published 0.2.134 transport — and there is no
supported way to hand the SDK a complete child environment. Running the SDK here
turns that into a non-problem: **this** process's ``os.environ`` is the allowlist
Cofferdam built, so the SDK's merge produces the allowlist. The guarantee rests
on Cofferdam's own ``Popen`` rather than on an SDK field, and it is checked again
here, at the top of :func:`main`, before anything from the SDK is constructed.

The reasoning behind choosing this over a custom ``Transport`` is recorded in
:mod:`.hostenv`, next to the allowlist it produced.

Three properties this file holds
--------------------------------

**Nothing unnormalized leaves.** Every event written to stdout has already been
through the normalizer and the bounded event constructors. There is no code path
here that serializes an SDK object, and :mod:`.hostproto` has no message type
that could carry one.

**stdout is the protocol and nothing else.** Anything this process might
otherwise print would corrupt a frame, so it prints nothing: there is no
``print`` in this file, no logging configuration, and the parent gives the child
a discarded stderr.

**It cannot outlive its work.** The session ends at its first terminal event, the
loop exits, and the process returns. A parent that goes away closes stdin, which
also ends the loop — so a daemon restart cannot leave a helper holding a
subscription session.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, Optional

#: How long the main loop sleeps between drains. Short enough that a phone
#: polling a task sees activity promptly, long enough that an idle helper is not
#: a busy loop on somebody's workstation.
POLL_INTERVAL_SECONDS = 0.1

#: How long the helper waits for its first command before giving up. A parent
#: that launched a helper and then died should not leave one waiting forever.
FIRST_COMMAND_TIMEOUT_SECONDS = 120.0

#: A hard ceiling on one helper's lifetime, independent of everything else. The
#: session has its own bounds — a start timeout, a question timeout, the SDK's own
#: turn and budget limits — and this is the backstop for the case where all of
#: them are somehow still running.
MAX_LIFETIME_SECONDS = 6 * 60 * 60.0

#: Exit codes. Zero means the session ran and reported; everything else says
#: which boundary refused, so a parent that sees a dead helper knows whether to
#: report a configuration problem or a provider one.
EXIT_OK = 0
EXIT_ENVIRONMENT = 3
EXIT_PROTOCOL = 4

#: What the command queue yields when a tick passed with nothing on it. A named
#: object rather than ``None``, because ``None`` already means something specific
#: on this queue — the parent closed stdin — and one sentinel doing two jobs is
#: how a closed pipe ends up read as an idle tick.
_IDLE = object()


def _write(stream: Any, payload: Dict[str, Any]) -> bool:
    """One protocol line on stdout. ``False`` when the parent has gone away."""
    from . import hostproto

    try:
        stream.write(hostproto.encode_line(payload))
        stream.flush()
    except (OSError, ValueError):
        return False
    return True


def _read_commands(stream: Any, queue: "Queue") -> None:
    """Consume stdin, one bounded line at a time, until it closes.

    Malformed lines are dropped rather than raising: a parent and a helper from
    slightly different builds should degrade to "that command did nothing", not
    to a dead session holding a subscription open.
    """
    from . import hostproto

    try:
        for raw in stream:
            if len(raw) > hostproto.MAX_LINE_BYTES:
                continue
            parsed = hostproto.decode_line(raw)
            if parsed is None:
                continue
            name = parsed.get("command")
            if name in hostproto.COMMANDS:
                queue.put(parsed)
    except (OSError, ValueError):  # pragma: no cover - the pipe went away
        pass
    finally:
        queue.put(None)


def _verify_own_environment() -> Optional[str]:
    """Confirm this process's environment is the one Cofferdam meant to build.

    The second half of the guarantee, and it is not ceremony. The first half is
    the parent passing ``env=`` to ``Popen``; this is the child refusing to run
    if what arrived is not what that produces. A helper started by hand, by a
    supervisor, or by a future caller that forgot the environment argument stops
    here rather than inheriting a shell's environment and handing it to an agent.

    The bootstrap names are dropped first, so that after this returns the process
    environment is exactly the session environment — which is exactly what the
    SDK will merge its own two names into.
    """
    from . import hostenv

    hostenv.drop_bootstrap_names()
    try:
        hostenv.verify_child_environment(os.environ, include_bootstrap=False)
    except hostenv.EnvironmentPolicyError as exc:
        # The message names a key, never a value — see the exception's own
        # docstring. Safe to hand back to the parent, which puts it in an event.
        return str(exc)
    return None


def _resolve_cli_path(value: Any) -> Optional[Path]:
    """The CLI path from the start command, or ``None``.

    ``None`` is a legitimate answer and means "let the SDK choose", which on a
    host with no CLI installed is what happens anyway.

    The value's provenance is the registry's fixed host search — one program
    name, a fixed directory order, no caller-supplied component — passed down
    through the adapter. There is no request field anywhere above it that carries
    a path. It is nevertheless re-checked here for the same reason the project
    root is re-verified before every task: the check that matters is the one
    closest to the work, and a binary can be uninstalled between a daemon
    starting and a task running.
    """
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    try:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    except OSError:  # pragma: no cover - platform dependent
        return None
    return None


def main(argv: Optional[list] = None) -> int:
    """Run one delegated session and report it. Returns a process exit code.

    Takes no arguments and reads none: ``argv`` exists so a test can call this
    function, and an argument vector with anything in it is refused. Everything
    this process needs arrives either as its working directory (the project
    root), its environment (built by :mod:`.hostenv`), or the ``start`` command
    on the protocol — and none of those three is a place a request value can
    reach.
    """
    from . import hostproto
    from .session import SdkSession, SessionRefused

    if argv:
        return EXIT_PROTOCOL

    problem = _verify_own_environment()
    stdout = sys.stdout
    if problem is not None:
        _write(stdout, hostproto.message(hostproto.MESSAGE_ERROR, detail=problem))
        return EXIT_ENVIRONMENT

    commands: "Queue" = Queue()
    reader = threading.Thread(
        target=_read_commands, args=(sys.stdin, commands), name="cofferdam-sdk-host-in",
        daemon=True,
    )
    reader.start()

    if not _write(stdout, hostproto.message(hostproto.MESSAGE_READY)):
        return EXIT_PROTOCOL

    session: Optional[SdkSession] = None
    #: Whether the parent has already been told about the turn that is currently
    #: complete. Reset when a new turn starts, so each turn is announced once
    #: rather than on every poll of an idle session.
    announced_turn = False
    deadline = time.monotonic() + MAX_LIFETIME_SECONDS
    waiting_for_first = time.monotonic() + FIRST_COMMAND_TIMEOUT_SECONDS

    try:
        while True:
            if time.monotonic() > deadline:  # pragma: no cover - the ceiling
                _write(
                    stdout,
                    hostproto.message(
                        hostproto.MESSAGE_ERROR, detail="this session ran out of time"
                    ),
                )
                break
            if session is None and time.monotonic() > waiting_for_first:
                return EXIT_PROTOCOL

            try:
                command: Any = commands.get(timeout=POLL_INTERVAL_SECONDS)
            except Empty:
                command = _IDLE

            if command is None:
                # stdin closed: the parent is gone. Nothing more can be asked of
                # this session and nobody is listening to what it says.
                break
            if command is not _IDLE:
                name = command.get("command")
                if name == hostproto.COMMAND_CLOSE:
                    break
                if name == hostproto.COMMAND_START:
                    if session is not None:
                        _write(
                            stdout,
                            hostproto.message(
                                hostproto.MESSAGE_ERROR,
                                detail="this helper has already started a session",
                            ),
                        )
                        break
                    session = _start_session(stdout, command, SdkSession, SessionRefused)
                    if session is None:
                        break
                elif session is None:
                    _write(
                        stdout,
                        hostproto.message(
                            hostproto.MESSAGE_ERROR,
                            detail="this helper has no session yet",
                        ),
                    )
                elif name == hostproto.COMMAND_ANSWER:
                    _deliver_answer(session, command)
                elif name == hostproto.COMMAND_FOLLOWUP:
                    _deliver_followup(stdout, session, command)
                elif name == hostproto.COMMAND_CANCEL:
                    session.request_cancel()

            if session is None:
                continue
            if not _flush(stdout, session):
                break
            # A turn that has just ended is announced once, and the loop keeps
            # going. This is the line that used to read `if session.finished:
            # break` for both cases, which is how a finished *turn* ended a
            # session that was still perfectly able to take another message.
            if session.turn_complete and not announced_turn:
                announced_turn = True
                if not _write(
                    stdout,
                    hostproto.message(
                        hostproto.MESSAGE_TURN_COMPLETE,
                        provider_session_id=session.provider_session_id,
                        turn=session.turn_number,
                    ),
                ):
                    break
            elif not session.turn_complete:
                announced_turn = False
            if session.finished:
                break
    finally:
        if session is not None:
            _flush(stdout, session)
            try:
                session.close()
            except Exception:  # pragma: no cover - tidying must not fail an exit
                pass
        _write(stdout, hostproto.message(hostproto.MESSAGE_FINISHED))
    return EXIT_OK


def _start_session(stdout: Any, command: Dict[str, Any], factory: Any, refusal: Any):
    """Build and start the one session this helper owns, or report why not."""
    from . import hostproto

    task_id = command.get("task_id")
    prompt = command.get("prompt")
    if not isinstance(task_id, str) or not isinstance(prompt, str) or not prompt:
        _write(
            stdout,
            hostproto.message(
                hostproto.MESSAGE_ERROR, detail="that start command is not usable"
            ),
        )
        return None

    session = factory(
        task_id=task_id,
        # The project root arrived as this process's working directory, set by
        # the parent's `Popen(cwd=…)` from a root the server resolved and
        # verified. It is never parsed out of a string on this channel.
        project_root=Path.cwd(),
        cli_path=_resolve_cli_path(command.get("cli_path")),
    )
    try:
        session.start(prompt)
    except refusal as declined:
        _write(
            stdout,
            hostproto.message(hostproto.MESSAGE_ERROR, detail=str(declined)),
        )
        return None
    except Exception:
        _write(
            stdout,
            hostproto.message(
                hostproto.MESSAGE_ERROR,
                detail="the Claude Agent SDK session could not be started",
            ),
        )
        return None

    _write(
        stdout,
        hostproto.message(
            hostproto.MESSAGE_STARTED,
            provider_session_id=session.provider_session_id,
        ),
    )
    return session


def _deliver_answer(session: Any, command: Dict[str, Any]) -> None:
    """Route one answer to the question it names. Silent when it does not match.

    A mismatched token is not reported back as an error, and that is deliberate:
    the parent already knows whether it believed a question was open, the answer
    route above it has already refused or accepted on its own record, and a
    second contradicting opinion arriving asynchronously would be a race with no
    correct resolution.
    """
    token = command.get("token")
    answer = command.get("answer")
    if isinstance(token, str) and isinstance(answer, str):
        session.submit_answer(token, answer)


def _deliver_followup(stdout: Any, session: Any, command: Dict[str, Any]) -> None:
    """Start one more turn on the session this helper owns.

    Unlike :func:`_deliver_answer`, a refusal *is* reported back. The two
    differ because their races differ: an answer that no longer matches is an
    ordinary supersession the parent already knows about, while a follow-up the
    session would not take is a message somebody typed that went nowhere — and
    the parent has to be able to tell them so rather than leave a task sitting
    in ``running`` with nothing running.

    The command carries text and nothing else. There is no session id on it and
    no way to add one: this process holds one client and cannot name another.
    """
    from . import hostproto
    from .session import SessionRefused

    followup = command.get("followup")
    if not isinstance(followup, str) or not followup:
        _write(
            stdout,
            hostproto.message(
                hostproto.MESSAGE_ERROR, detail="that follow-up is not usable"
            ),
        )
        return
    try:
        session.send_followup(followup)
    except SessionRefused as declined:
        # Cofferdam's words already — see the exception's own docstring — so
        # this is safe to hand back and put in an event.
        _write(stdout, hostproto.message(hostproto.MESSAGE_ERROR, detail=str(declined)))
    except Exception:
        _write(
            stdout,
            hostproto.message(
                hostproto.MESSAGE_ERROR,
                detail="the follow-up could not be delivered to Claude",
            ),
        )


def _flush(stdout: Any, session: Any) -> bool:
    """Write everything the session has produced. ``False`` if the pipe closed."""
    from . import hostproto

    for event in session.drain():
        if not _write(stdout, hostproto.event_payload(event)):
            return False
    return True


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main(sys.argv[1:]))


__all__ = [
    "EXIT_ENVIRONMENT",
    "EXIT_OK",
    "EXIT_PROTOCOL",
    "FIRST_COMMAND_TIMEOUT_SECONDS",
    "MAX_LIFETIME_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "main",
]
