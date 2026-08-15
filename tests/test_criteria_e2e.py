"""M2K PR6 — one end-to-end pass, isolated home and real Git.

The unit tests each pin one rule. This walks the whole path in order — real
repository, real service, real store, real restart — and asserts the properties
that only appear when every layer runs together: criteria and the PR4 baseline
both durable before the worker's first instruction, the turn opening afterwards,
the snapshot frozen through a refusal and a retry and a restart, a follow-up
receiving its own snapshot, and a historical turn still reading
``legacy_unknown``.

It also asserts what is **not** here, because the absence is the deliverable:
no evaluation record, no met/not_met, no verdict, no check runner, no command,
no HTTP route, no bridge change.

No provider, no model, no network, no deployment.
"""

from __future__ import annotations

import ast
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from typing import Any, Dict, List, Sequence

from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from cofferdam.workstation.tasks.criteria import (
    CRITERIA_LEGACY_UNKNOWN,
    CRITERIA_NOT_PROVIDED,
    CRITERIA_PRESENT,
    KIND_MANUAL,
)
from cofferdam.workstation.tasks.evidence import ASSEMBLER_VERSION
from cofferdam.workstation.tasks.gitbaseline import (
    DISPATCH_REFUSED,
    DISPATCH_STARTED,
    DISPATCH_TURN_OPENED,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION

from ._task_doubles import PROJECT_ID, TaskTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]

GIT = shutil.which("git")
GIT_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}

CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "src/app.py"},
    {
        "kind": "evidence",
        "predicate": "path_operation",
        "path": "src/new.py",
        "operation": "created",
        "description": "the new module has to exist",
    },
    {
        "kind": "evidence",
        "predicate": "rename",
        "path": "src/old.py",
        "to_path": "src/renamed.py",
    },
    {"kind": "manual", "description": "somebody looks at the page and it renders"},
]
FOLLOWUP_CRITERIA = [
    {"kind": "evidence", "predicate": "path_changed", "path": "tests/test_app.py"},
]


class WalkingAdapter(TaskAdapter):
    """Records what was durable each time it was handed control."""

    adapter_id = "walker"
    display_name = "Walking adapter"
    description = "A test double."

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.refuse = False
        self.seen: List[Dict[str, Any]] = []

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True, followup=True, cancel=True, final_result=True
        )

    def available(self) -> bool:
        return True

    def session_available(self, task_id: str) -> bool:
        return True

    def _look(self, context: TaskContext, call: str) -> None:
        connection = sqlite3.connect("file:%s?mode=ro" % self._db_path, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            self.seen.append(
                {
                    "call": call,
                    "criteria": [
                        dict(r)
                        for r in connection.execute(
                            "SELECT * FROM task_turn_criteria WHERE task_id = ?"
                            " ORDER BY turn_number",
                            (context.task_id,),
                        )
                    ],
                    "items": [
                        dict(r)
                        for r in connection.execute(
                            "SELECT * FROM task_turn_criterion_items"
                            " WHERE task_id = ? ORDER BY turn_number, ordinal",
                            (context.task_id,),
                        )
                    ],
                    "baselines": [
                        dict(r)
                        for r in connection.execute(
                            "SELECT * FROM task_turn_git_baselines WHERE task_id = ?"
                            " ORDER BY turn_number",
                            (context.task_id,),
                        )
                    ],
                    "turns": [
                        r["turn_number"]
                        for r in connection.execute(
                            "SELECT turn_number FROM task_turns WHERE task_id = ?",
                            (context.task_id,),
                        )
                    ],
                }
            )
        finally:
            connection.close()

    def start(self, context: TaskContext) -> AdapterOutcome:
        self._look(context, "start")
        if self.refuse:
            raise AdapterRefusal("the session refused")
        return AdapterOutcome(
            events=(AdapterEvent(text="walked"),),
            requested_state="ready_for_followup",
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        self._look(context, "send_followup")
        if self.refuse:
            raise AdapterRefusal("the session refused")
        return AdapterOutcome(
            events=(AdapterEvent(text="walked"),),
            requested_state="ready_for_followup",
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        return AdapterOutcome(events=(), requested_state="cancelled")


@unittest.skipIf(GIT is None, "git is not installed")
class CriteriaEndToEnd(TaskTestCase):
    project_adapters = ("walker", "validation")

    def extra_adapters(self) -> Sequence[TaskAdapter]:
        self.walker = WalkingAdapter(self.home / "state" / "tasks" / "tasks.sqlite3")
        return (self.walker,)

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        (self.project_root / "src").mkdir(parents=True, exist_ok=True)
        (self.project_root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")

    def git(self, *args):
        subprocess.run(
            [GIT, *args],
            cwd=str(self.project_root),
            check=True,
            capture_output=True,
            env={**GIT_ENV, "HOME": str(self.project_root)},
        )

    def start(self, criteria=CRITERIA):
        row, _ = self.service.create_task(
            prompt="do the work",
            project_id=PROJECT_ID,
            adapter_id="walker",
            origin="pwa",
            criteria=criteria,
        )
        return row

    def db(self):
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    # -- 1..7 the happy path -------------------------------------------------

    def test_the_walk(self):
        """One task, one follow-up, in order, with every step asserted."""
        # 1. schema v7
        # At least, not exactly: each additive bump since this walk was written
        # leaves it true. M2K PR7 took it to 8. The literal pin for the current
        # version lives in `test_task_core.py`.
        self.assertGreaterEqual(SCHEMA_VERSION, 7)
        self.assertGreaterEqual(self.service.store.storage_health()["schema_version"], 7)

        # 2-6. create with criteria; the adapter's first instruction sees both
        # pre-work facts durable over a separate read-only connection, and no
        # turn row.
        row = self.start()
        first = self.walker.seen[0]
        self.assertEqual(first["call"], "start")
        self.assertEqual(len(first["criteria"]), 1)
        self.assertEqual(first["criteria"][0]["criteria_state"], CRITERIA_PRESENT)
        self.assertEqual(first["criteria"][0]["criterion_count"], 4)
        self.assertEqual(first["criteria"][0]["dispatch_state"], DISPATCH_STARTED)
        self.assertEqual(len(first["items"]), 4)
        self.assertEqual([i["ordinal"] for i in first["items"]], [1, 2, 3, 4])
        self.assertEqual(len(first["baselines"]), 1)
        self.assertEqual(first["baselines"][0]["dispatch_state"], DISPATCH_STARTED)
        self.assertEqual(first["turns"], [])

        # 7. the actual turn opens afterwards and binds the snapshot
        self.assertEqual([t.turn_number for t in self.service.store.turns(row.task_id)], [1])
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(snapshot.dispatch_state, DISPATCH_TURN_OPENED)

        # 8. immutable
        frozen = dict(
            self.db().execute(
                "SELECT * FROM task_turn_criteria WHERE task_id=? AND turn_number=1",
                (row.task_id,),
            ).fetchone()
        )

        # 9. a follow-up turn gets a new snapshot; turn one is untouched
        self.service.send_followup(row.task_id, "next", criteria=FOLLOWUP_CRITERIA)
        second = self.service.turn_criteria(row.task_id, 2)
        self.assertEqual(second.state, CRITERIA_PRESENT)
        self.assertEqual([c.path for c in second.criteria], ["tests/test_app.py"])
        self.assertNotEqual(snapshot.snapshot_id, second.snapshot_id)
        self.assertNotEqual(snapshot.fingerprint, second.fingerprint)
        self.assertEqual(
            dict(
                self.db().execute(
                    "SELECT * FROM task_turn_criteria WHERE task_id=? AND turn_number=1",
                    (row.task_id,),
                ).fetchone()
            ),
            frozen,
        )

        # 14. the criterion paths are project-relative, with no host root in them
        for item in self.db().execute(
            "SELECT path, to_path FROM task_turn_criterion_items WHERE task_id=?",
            (row.task_id,),
        ):
            for value in (item["path"], item["to_path"]):
                if value is None:
                    continue
                self.assertFalse(value.startswith("/"))
                self.assertNotIn(str(self.home), value)

        # 15. the manual criterion is stored and is not decided
        manual = [c for c in snapshot.criteria if c.kind == KIND_MANUAL]
        self.assertEqual(len(manual), 1)
        self.assertFalse(manual[0].evidence_evaluable)
        self.assertIsNone(manual[0].predicate)

        # 16. the fingerprint is stable across a restart
        self.restart()
        after = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(after.fingerprint, snapshot.fingerprint)
        self.assertEqual(after.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(
            [c.criterion_id for c in after.criteria],
            [c.criterion_id for c in snapshot.criteria],
        )

    # -- 10-11 refusal, retry and crash --------------------------------------

    def test_a_refusal_and_a_retry_preserve_the_snapshot(self):
        self.walker.refuse = True
        row = self.start()
        stored = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(stored.dispatch_state, DISPATCH_REFUSED)
        self.assertEqual(stored.state, CRITERIA_PRESENT)

        from cofferdam.workstation.tasks.criteria import validate_criteria

        self.service.store.reserve_turn_criteria(
            row.task_id,
            validate_criteria([{"kind": "manual", "description": "something else"}]),
            recorded_at="2026-08-15T12:00:00Z",
        )
        retried = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(retried.snapshot_id, stored.snapshot_id)
        self.assertEqual(retried.fingerprint, stored.fingerprint)
        self.assertEqual(len(retried.criteria), 4)

    def test_a_crash_preserves_the_frozen_snapshot(self):
        self.walker.refuse = True
        row = self.start()
        before = self.service.turn_criteria(row.task_id, 1)
        self.restart()
        after = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(after.dispatch_state, DISPATCH_REFUSED)

    # -- 12-13 the two kinds of absence --------------------------------------

    def test_a_task_with_no_criteria_records_not_provided(self):
        row = self.start(None)
        self.assertEqual(
            self.walker.seen[0]["criteria"][0]["criteria_state"], CRITERIA_NOT_PROVIDED
        )
        self.assertEqual(
            self.service.turn_criteria(row.task_id, 1).state, CRITERIA_NOT_PROVIDED
        )

    def test_a_historical_turn_reads_legacy_unknown(self):
        """A turn written straight into the tables, as a pre-v7 turn would be."""
        row = self.start(None)
        path = self.home / "state" / "tasks" / "tasks.sqlite3"
        with sqlite3.connect(str(path)) as db:
            db.execute(
                "DELETE FROM task_turn_criteria WHERE task_id=? AND turn_number=1",
                (row.task_id,),
            )
        self.restart()
        snapshot = self.service.turn_criteria(row.task_id, 1)
        self.assertEqual(snapshot.state, CRITERIA_LEGACY_UNKNOWN)
        self.assertNotEqual(snapshot.state, CRITERIA_NOT_PROVIDED)
        self.assertFalse(snapshot.recorded)

    # -- 17..22 what this PR deliberately does not contain --------------------

    def test_there_is_no_evaluation_record_anywhere(self):
        self.start()
        with sqlite3.connect(
            str(self.home / "state" / "tasks" / "tasks.sqlite3")
        ) as db:
            names = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for forbidden in (
            "task_evaluations",
            "task_evaluation_records",
            "task_criterion_results",
            "task_verdicts",
            "task_checks",
        ):
            self.assertNotIn(forbidden, names)

    def test_no_criterion_carries_a_result(self):
        row = self.start()
        with sqlite3.connect(
            str(self.home / "state" / "tasks" / "tasks.sqlite3")
        ) as db:
            columns = {
                r[1] for r in db.execute("PRAGMA table_info(task_turn_criterion_items)")
            }
        for forbidden in ("result", "met", "outcome", "status", "verdict", "passed"):
            self.assertNotIn(forbidden, columns)

    def test_the_evidence_bundle_is_exactly_pr5s(self):
        """Criteria are not an evidence input, so nothing about a bundle moves."""
        row = self.start()
        self.assertEqual(ASSEMBLER_VERSION, 3)
        bundle = self.service.evidence_bundle(row.task_id, 1)
        self.assertIsNotNone(bundle)
        payload = bundle.to_dict()
        self.assertEqual(payload["assembler_version"], 3)
        for forbidden in ("criteria", "criterion", "acceptance_criteria", "evaluation"):
            self.assertNotIn(forbidden, payload)

    def test_there_is_no_http_route_for_criteria(self):
        """Scanned from the syntax tree, so a decorator string cannot hide."""
        source = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        routes = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for argument in decorator.args:
                    if isinstance(argument, ast.Constant) and isinstance(
                        argument.value, str
                    ):
                        routes.append(argument.value)
        self.assertTrue(routes, "no routes were found to scan")
        for route in routes:
            self.assertNotIn("criteri", route.lower(), route)

    def test_the_task_body_allowlist_has_no_criteria_key(self):
        source = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '{"project_id", "adapter_id", "prompt", "client_request_id", "title"}',
            source,
        )
        self.assertNotIn('"criteria"', source)

    def test_the_actions_bridge_is_unchanged(self):
        bridge = REPO_ROOT / "cofferdam" / "actions_bridge"
        found = []
        for path in sorted(bridge.rglob("*.py")):
            if "criteri" in path.read_text(encoding="utf-8").lower():
                found.append(path.name)
        self.assertEqual(found, [])

    def test_no_command_runner_exists(self):
        package = REPO_ROOT / "cofferdam" / "workstation" / "tasks"
        for path in sorted(package.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if path.name in ("gitbaseline.py", "gitrange.py"):
                continue  # the two host-owned Git probes, PR4 and PR5
            self.assertNotIn("subprocess.", source, path.name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
