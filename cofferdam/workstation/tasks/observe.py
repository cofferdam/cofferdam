"""Watching one task from the workstation, without being able to touch it.

    python -m cofferdam.workstation.tasks.observe <task_id>

A developer working on an agent adapter needs to see what the thing is doing.
The obvious way to get that is a terminal that owns the process — and that is
precisely the wrong shape, because the moment a terminal owns the process, the
terminal's lifetime is the task's lifetime, closing a window kills work, and
"who is in charge of this task" has two answers.

So this observes and nothing else. It opens the task database **read-only**,
prints what Task Core recorded, and follows new events as they are appended. It
is not part of the daemon, has no route, and holds no authority.

What it is structurally incapable of
------------------------------------

* **Starting anything.** There is no import of :mod:`subprocess` here, and no
  call that could reach one.
* **Changing anything.** The connection is opened with ``mode=ro``, so SQLite
  itself refuses a write — this is not a convention the code follows, it is a
  property of the handle.
* **Stopping the task.** Closing this program, or killing it, does nothing to
  the task. The daemon owns the process; this owns a file descriptor.
* **Being reached from a phone.** It is a local command. It is not registered
  as a route, it does not listen on a socket, and enabling it is running it.

What it will not show
---------------------

Only what Task Core already stored, which is already bounded and already
sanitised by the adapter's parser: state, activity, meaningful output, the final
result, and evidence labelled by source. There is no raw CLI frame here, no
environment, no credential, and no hidden reasoning — the parser never collected
any, so there is nothing for this to print.

Content goes to **stdout only**, for the person who typed the command. Nothing
here writes to the journal.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

#: How often to look for new events. Slow enough to be invisible in load terms,
#: fast enough to feel live to somebody watching a task run.
POLL_SECONDS = 0.5

#: Bounds, applied again here rather than trusted from upstream. This program is
#: the last thing between a database row and somebody's terminal.
MAX_LINE_CHARS = 2000
MAX_EVENTS_PER_POLL = 200

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})


def database_path(home: Optional[str] = None) -> Path:
    """Where the task store lives, derived the same way the daemon derives it.

    The directory and file names are **imported** from :mod:`.store` rather than
    written out again here. A copy would be right on the day it was typed and
    wrong on the day somebody renamed the file — which is exactly what happened
    while this module was being written, and cost a confusing "no task database"
    against a database that was plainly there.
    """
    import os

    from .store import DATABASE_FILENAME, TASKS_DIRNAME

    root = Path(home or os.environ.get("COFFERDAM_HOME") or (Path.home() / "cofferdam"))
    return root / "state" / TASKS_DIRNAME / DATABASE_FILENAME


def _connect(path: Path) -> sqlite3.Connection:
    """A read-only handle, refused by SQLite rather than by good intentions.

    ``mode=ro`` on a URI connection makes every write fail at the driver. That
    matters more than a promise in a docstring: it means a future edit that
    tries to "just update one row" from this program does not work, instead of
    working and quietly becoming a second writer to a store whose whole design
    rests on having exactly one.
    """
    if not path.is_file():
        raise SystemExit("no task database at " + str(path))
    connection = sqlite3.connect(
        "file:" + str(path) + "?mode=ro", uri=True, timeout=5.0
    )
    connection.row_factory = sqlite3.Row
    return connection


def _bounded(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    return text[:MAX_LINE_CHARS]


def _evidence(raw: object) -> str:
    """Evidence as a person needs to read it: what it is, and *who looked*.

    The source is the whole point of printing this at all. "Claude says it
    changed a file" and "Cofferdam ran git status and saw the file change" are
    different claims, and a developer watching a task is exactly the person who
    needs to be able to tell them apart at a glance.
    """
    if not raw:
        return ""
    try:
        items = json.loads(str(raw))
    except ValueError:
        return ""
    if not isinstance(items, list):
        return ""
    parts: List[str] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "?")
        mark = "observed" if item.get("verified") else "claimed"
        label = str(item.get("identifier") or item.get("operation") or "-")
        parts.append(label[:80] + " [" + source + "/" + mark + "]")
    return "; ".join(parts)


def _print_task(connection: sqlite3.Connection, task_id: str) -> Optional[sqlite3.Row]:
    row = connection.execute(
        "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if row is None:
        return None
    keys = row.keys()
    print("task     " + _bounded(row["task_id"]))
    print("adapter  " + _bounded(row["adapter_id"]))
    print("project  " + _bounded(row["project_id"]))
    print("state    " + _bounded(row["state"]) + (
        "  (" + _bounded(row["waiting_reason"]) + ")" if row["waiting_reason"] else ""
    ))
    if "title" in keys and row["title"]:
        print("title    " + _bounded(row["title"]))
    print("-" * 72)
    return row


def _print_events(connection: sqlite3.Connection, task_id: str, after: int) -> int:
    rows = connection.execute(
        "SELECT sequence, created_at, event_type, actor, source, text, detail,"
        " evidence_json FROM task_events WHERE task_id = ? AND sequence > ?"
        " ORDER BY sequence ASC LIMIT ?",
        (task_id, after, MAX_EVENTS_PER_POLL),
    ).fetchall()
    for row in rows:
        stamp = _bounded(row["created_at"])[11:19]
        head = stamp + "  " + _bounded(row["event_type"])
        text = _bounded(row["text"])
        detail = _bounded(row["detail"])
        line = head
        if text:
            line += "  " + text.replace("\n", "\n" + " " * 20)
        if detail:
            line += "  (" + detail + ")"
        print(line)
        evidence = _evidence(row["evidence_json"])
        if evidence:
            print(" " * 20 + "evidence: " + evidence)
        after = int(row["sequence"])
    return after


def _current_state(connection: sqlite3.Connection, task_id: str) -> Optional[str]:
    row = connection.execute(
        "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return None if row is None else str(row["state"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cofferdam.workstation.tasks.observe",
        description=(
            "Follow one Cofferdam task from this workstation, read-only. It "
            "starts nothing, changes nothing, and closing it does not affect "
            "the task."
        ),
    )
    parser.add_argument("task_id", help="the task id issued by the server")
    parser.add_argument("--home", help="COFFERDAM_HOME (default: the environment)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="print what is recorded and exit, instead of following",
    )
    parser.add_argument(
        "--result",
        action="store_true",
        help="also print the final result when the task reaches a terminal state",
    )
    args = parser.parse_args(argv)

    path = database_path(args.home)
    connection = _connect(path)
    try:
        row = _print_task(connection, args.task_id)
        if row is None:
            print("no such task: " + args.task_id, file=sys.stderr)
            return 2

        cursor = _print_events(connection, args.task_id, 0)
        state = str(row["state"])
        if args.once or state in TERMINAL_STATES:
            return _finish(connection, args, state)

        try:
            while True:
                time.sleep(POLL_SECONDS)
                cursor = _print_events(connection, args.task_id, cursor)
                state = _current_state(connection, args.task_id) or state
                if state in TERMINAL_STATES:
                    return _finish(connection, args, state)
        except KeyboardInterrupt:
            # Ctrl-C ends the watching, not the task. Said out loud, because the
            # habit this program exists to replace is one where it would have
            # ended both.
            print()
            print("stopped watching. the task is unaffected.")
            return 0
    finally:
        connection.close()


def _finish(connection: sqlite3.Connection, args, state: str) -> int:
    print("-" * 72)
    print("state    " + state)
    if args.result:
        row = connection.execute(
            "SELECT final_result FROM tasks WHERE task_id = ?", (args.task_id,)
        ).fetchone()
        if row is not None and row["final_result"]:
            print("result   " + _bounded(row["final_result"]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
