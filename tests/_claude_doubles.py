"""A fake Claude Code CLI that speaks the real protocol.

The important choice in this file is that it is a **real program in a real
process**, not a mock. A `unittest.mock` standing in for `subprocess.Popen`
would let every process test pass while proving nothing about the parts that
actually matter here: that a pid is real, that its start time comes from
``/proc``, that SIGTERM reaches a process group and not the test runner, that a
concurrency limit stops a second *process* rather than a second dictionary
entry.

So this writes a small Python script to a temporary directory and the adapter
launches it exactly the way it would launch ``claude``. The script speaks the
subset of the stream-json protocol the adapter reads, verified against the
installed CLI 2.1.221 during development: a ``system/init`` frame carrying the
session id from ``--session-id``, then one ``result`` frame per user message
read from stdin.

It calls no model, reaches no network, and imports nothing but the standard
library.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

#: The fake CLI. Written to disk and executed for real.
#:
#: ``BEHAVIOUR`` is read from the environment because the *adapter* builds the
#: argv, and a test that needed to add a flag to control the fake would be a
#: test that had to weaken the argv template to run — which is precisely the
#: thing being protected. The environment variable is added by the test's own
#: environment mapping, never by the adapter.
SCRIPT = '''#!/usr/bin/env python3
"""A fake Claude Code CLI. Speaks stream-json; calls nothing."""
import json, os, sys, time

argv = sys.argv[1:]


def flag(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


session = flag("--session-id", "no-session")
behaviour = os.environ.get("FAKE_CLAUDE_BEHAVIOUR", "complete")
# Proof, recorded by the fake itself, that the prompt never arrived as an
# argument. The test reads this file rather than trusting an assertion made
# from the same process that built the argv.
record = os.environ.get("FAKE_CLAUDE_RECORD")
if record:
    with open(record, "w", encoding="utf-8") as handle:
        json.dump({"argv": argv, "cwd": os.getcwd(),
                   "env": sorted(os.environ)}, handle)


def emit(frame):
    sys.stdout.write(json.dumps(frame) + "\\n")
    sys.stdout.flush()


def init():
    emit({"type": "system", "subtype": "init", "session_id":
          "wrong-session" if behaviour == "session_mismatch" else session,
          "uuid": "u", "tools": [], "model": "fake"})


if behaviour == "silent_start":
    time.sleep(30)
    sys.exit(0)

if behaviour == "exit_zero_no_result":
    init()
    sys.stdin.readline()
    sys.exit(0)

init()

turn = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    turn += 1
    text = message.get("message", {}).get("content")

    if behaviour == "hang":
        time.sleep(300)

    if behaviour == "oversized":
        emit({"type": "assistant", "message": {"role": "assistant", "content":
              [{"type": "text", "text": "x" * (400 * 1024)}]}})

    if behaviour == "unknown_frames":
        emit({"type": "totally_unknown", "danger": {"nested": ["payload"]}})
        emit({"type": "system", "subtype": "unrecognised", "x": 1})
        sys.stdout.write("this is not json at all\\n")
        sys.stdout.flush()

    if behaviour == "ansi":
        emit({"type": "assistant", "message": {"role": "assistant", "content":
              [{"type": "text",
                "text": "\\x1b[31mred\\x1b[0m <script>alert(1)</script>\\x07"}]}})

    if behaviour == "thinking":
        emit({"type": "assistant", "message": {"role": "assistant", "content":
              [{"type": "thinking", "thinking": "SECRET-REASONING-TOKEN"},
               {"type": "text", "text": "visible answer"}]}})

    if behaviour == "tools":
        emit({"type": "assistant", "message": {"role": "assistant", "content":
              [{"type": "tool_use", "name": "Edit", "input": {"file": "a.py"}}]}})
        emit({"type": "user", "message": {"role": "user", "content":
              [{"type": "tool_result", "content": "ok", "is_error": False}]}})

    # Echo the received turn back so a test can prove which text arrived, and
    # in which session, without the adapter being asked to report it.
    emit({"type": "assistant", "message": {"role": "assistant",
          "content": [{"type": "text", "text": "received: " + str(text)}]}})

    if behaviour == "error_result":
        emit({"type": "result", "subtype": "error_during_execution",
              "is_error": True, "result": "something went wrong",
              "session_id": session, "uuid": "r", "permission_denials": []})
        continue

    if behaviour == "denied":
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "could not do it", "session_id": session, "uuid": "r",
              "permission_denials": [{"tool_name": "Bash",
                                      "tool_input": {"command": "rm -rf /"}}]})
        continue

    if behaviour == "empty_result":
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "", "session_id": session, "uuid": "r",
              "permission_denials": []})
        continue

    emit({"type": "result", "subtype": "success", "is_error": False,
          "result": "turn " + str(turn) + " done: " + str(text),
          "session_id": session, "uuid": "r", "permission_denials": [],
          "stop_reason": "end_turn", "num_turns": turn,
          "total_cost_usd": 0.01})

sys.exit(0)
'''


class FakeClaude:
    """A temporary fake CLI on disk, plus the environment that configures it."""

    def __init__(self, directory: Path, behaviour: str = "complete") -> None:
        self.directory = directory
        self.path = directory / "claude"
        self.path.write_text(SCRIPT, encoding="utf-8")
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        self.behaviour = behaviour
        self.record_path = directory / "launch.json"

    def environment(self) -> dict:
        """The child environment: the adapter's own allowlist, plus the fake's knobs.

        Built from :func:`~...cli.build_environment` so that a test exercises
        the real allowlist rather than a convenient dictionary — if the
        allowlist stops carrying ``PATH``, these tests notice.
        """
        from cofferdam.workstation.tasks.adapters.claude_code import cli

        env = cli.build_environment()
        env["FAKE_CLAUDE_BEHAVIOUR"] = self.behaviour
        env["FAKE_CLAUDE_RECORD"] = str(self.record_path)
        env.setdefault("PATH", os.defpath)
        return env

    def launch_record(self) -> Optional[dict]:
        """What the fake saw when it started: argv, cwd, environment names."""
        try:
            return json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def make_fake(behaviour: str = "complete"):
    """A :class:`FakeClaude` in a temporary directory, plus its cleanup."""
    holder = tempfile.TemporaryDirectory()
    return FakeClaude(Path(holder.name), behaviour), holder


def always_authenticated(_executable, **_kwargs):
    from cofferdam.workstation.tasks.adapters.claude_code.cli import AuthStatus

    return AuthStatus(logged_in=True, method="claude.ai")


def never_authenticated(_executable, **_kwargs):
    from cofferdam.workstation.tasks.adapters.claude_code.cli import AuthStatus

    return AuthStatus(logged_in=False, method=None)


__all__ = [
    "FakeClaude",
    "SCRIPT",
    "always_authenticated",
    "make_fake",
    "never_authenticated",
]
