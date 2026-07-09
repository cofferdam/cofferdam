"""Command-line interface — skeleton.

This module owns three things that every later command depends on:

* argument parsing and command dispatch,
* the exit-code convention, and
* the stdout / stderr split.

Machine-readable output goes to stdout; everything human-facing or
diagnostic goes to stderr. The command registry is empty in this skeleton
— real commands (guard, dry-run, approve, apply, ...) register into it in
later versions. Nothing here performs guarding, execution, approval,
auditing, model calls, or any network I/O.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional, Sequence

from . import __version__

PROGRAM = "cofferdam"

# Exit-code convention. Kept deliberately small; later commands map their
# outcomes onto these.
EXIT_OK = 0
EXIT_USAGE = 2

# Command dispatch registry: name -> handler(args) -> exit code. Empty in
# the skeleton; this is the seam later versions extend. A handler receives
# the arguments that follow its command name and returns an exit code.
CommandHandler = Callable[[Sequence[str]], int]
COMMANDS: dict[str, CommandHandler] = {}

_HELP = f"""\
usage: {PROGRAM} [--version] [-h | --help] <command> [<args>]

A controlled workspace where AI agents propose, reviews weigh in, and humans
decide what crosses the boundary.

options:
  --version    print the {PROGRAM} version to stdout and exit
  -h, --help   print this help to stdout and exit

commands:
  (none yet - this is the v0.1 skeleton)
"""


def _write(stream, text: str) -> None:
    """Write ``text`` to ``stream``, ensuring exactly one trailing newline."""
    stream.write(text if text.endswith("\n") else text + "\n")


def _usage_error(message: str) -> int:
    """Report a usage error to stderr and return the usage exit code."""
    _write(sys.stderr, f"{PROGRAM}: {message}")
    _write(sys.stderr, f"try '{PROGRAM} --help'")
    return EXIT_USAGE


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns a process exit code; performs no I/O beyond
    writing to stdout/stderr.

    ``argv`` is the argument list *excluding* the program name. When
    ``None``, ``sys.argv[1:]`` is used.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        # No command given: show help on stdout and succeed. A bare
        # invocation is a request for orientation, not an error.
        _write(sys.stdout, _HELP)
        return EXIT_OK

    first = args[0]

    if first == "--version":
        # Bare version string only, so stdout stays trivially parseable.
        _write(sys.stdout, __version__)
        return EXIT_OK

    if first in ("-h", "--help"):
        _write(sys.stdout, _HELP)
        return EXIT_OK

    if first.startswith("-"):
        return _usage_error(f"unknown option: {first}")

    handler = COMMANDS.get(first)
    if handler is None:
        return _usage_error(f"unknown command: {first}")

    return handler(args[1:])
