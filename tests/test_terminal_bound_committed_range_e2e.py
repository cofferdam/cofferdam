"""M2K PR26 — the deployment-retry smoke, both evidence domains, end to end.

PR25's end-to-end module proved one criterion — ``path_exists`` — against an
asynchronous worker, and it passes. It could not have caught PR5's defect,
because a state predicate never consults the committed range.

This module is the retry smoke, and it is deliberately **discriminating**: one
criterion from each domain, in one human-authored snapshot, against one
asynchronous worker that does a different thing for each::

    path_exists("state-smoke.txt")     the worker *creates* the file
    path_changed("change-smoke.txt")   the worker *changes and commits* the file

A build that fixed only the final-state boundary answers the first honestly and
the second ``not_met``. A build that fixed only the committed range answers the
second honestly and the first ``not_met``. Only a build with both boundaries at
the terminal worker report answers both, which is what the deployment has to do.

The commit is not decoration. It is what makes ``change-smoke.txt`` invisible to
PR3's worktree domain, which is what leaves the committed range as the only
evidence that can answer the criterion at all.

Nothing is hand-built at either end:

* the **request** comes from the shipped ``web/tasks.js`` through
  ``tests/tasks_harness.js``, so what is posted is what the panel sends when
  somebody chooses an explicit root and adds two requirements;
* the **server** is the real ``create_app`` over real HTTP, with an
  asynchronous adapter whose worker runs between calls;
* the **read** is PR22's assessment route with PR21's acceptance section.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - stdlib-only CI path
    TestClient = None

from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
    git_evidence,
    observe_git,
)
from cofferdam.workstation.tasks.adapters.protocol import (
    AdapterCapabilities,
    AdapterOutcome,
    TaskAdapter,
)
from cofferdam.workstation.tasks.models import STATE_COMPLETED, STATE_RUNNING
from cofferdam.workstation.tasks.store import TaskStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_HARNESS = REPO_ROOT / "tests" / "tasks_harness.js"
DEVICE_TOKEN = "device-token-not-a-real-credential-0026"
PROJECT_ID = "demo"
STATE_TARGET = "state-smoke.txt"
CHANGE_TARGET = "change-smoke.txt"


def authored_request() -> dict:
    """The body the panel sends for an explicit root plus the two requirements."""
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("node is not installed")
    completed = subprocess.run(
        [node, str(TASKS_HARNESS), "authoring-state-and-change-smoke-request"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - harness failure
        raise AssertionError("harness failed: " + completed.stderr[:800])
    payload = json.loads(completed.stdout)
    assert not payload.get("uncaught"), payload.get("uncaught")
    posts = payload["posts"]
    assert len(posts) == 1, f"expected one request, got {len(posts)}"
    return posts[0]["body"]


class RetrySmokeWorker(TaskAdapter):
    """Asynchronous. Both effects land strictly after ``start`` returns.

    The terminal report carries the real ``git status`` observation, as the
    Claude Code adapter's does — so the worktree domain is genuinely closed and
    ``change-smoke.txt``, having been committed, is genuinely absent from it.
    """

    adapter_id = "validation"
    display_name = "Retry Smoke Worker"

    def __init__(self):
        self.root = None
        self._pending = None

    def capabilities(self):
        return AdapterCapabilities(
            start=True, followup=True, structured_progress=True, final_result=True
        )

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def start(self, context):
        self.root = Path(context.project_root)
        return AdapterOutcome(requested_state=STATE_RUNNING)

    def work(self):
        # Left in the working tree: PR25's domain answers this one.
        (self.root / STATE_TARGET).write_text("smoke\n", encoding="utf-8")
        # Changed *and committed*: the worktree goes clean, so only PR5's domain
        # can answer this one.
        (self.root / CHANGE_TARGET).write_text("after\n", encoding="utf-8")
        subprocess.run(
            ("git", "add", CHANGE_TARGET),
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ("git", "commit", "-qm", "the worker's commit"),
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        self._pending = STATE_COMPLETED

    def inspect(self, context):
        if self._pending is None:
            return AdapterOutcome()
        self._pending = None
        return AdapterOutcome(
            requested_state=STATE_COMPLETED,
            final_result="done",
            observations=git_evidence(observe_git(Path(context.project_root))),
        )


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class RetrySmokeEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app
        from cofferdam.workstation.tasks import build_registry
        from cofferdam.workstation.tasks.projects import load_projects
        from cofferdam.workstation.tasks.service import TaskService

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.root = self.home / "projects" / PROJECT_ID
        self.root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        # Tracked before the turn, so the worker's edit is a `modified` inside
        # the range rather than an untracked file it happens to add.
        (self.root / CHANGE_TARGET).write_text("before\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

        config = load_config(self.home)
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True}
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo",
                            "root": str(self.root),
                            "adapters": ["validation"],
                            "enabled": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = config
        self.worker = RetrySmokeWorker()
        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        self.service = TaskService(
            config,
            TaskStore(config),
            registry,
            projects=load_projects(config, registry.ids()),
        )
        self.addCleanup(self.service.store.close)
        self.app = create_app(
            config=config,
            token=DEVICE_TOKEN,
            adapter=StubAdapter(config),
            tasks=self.service,
        )
        self.client = TestClient(self.app)
        self.assertIs(self.service, self.app.state.tasks)

    def git(self, *arguments):
        subprocess.run(
            ("git",) + arguments, cwd=self.root, check=True, capture_output=True
        )

    def head(self):
        return subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def device(self) -> dict:
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def assessment_url(self, task_id: str, turn: int = 1) -> str:
        return "/api/tasks/" + task_id + "/turns/" + str(turn) + "/assessment"

    def create(self) -> str:
        body = authored_request()
        self.assertEqual(
            [
                ("path_exists", STATE_TARGET),
                ("path_changed", CHANGE_TARGET),
            ],
            [(item["predicate"], item["path"]) for item in body["criteria"]],
            "the shipped panel no longer authors the smoke's two requirements",
        )
        response = self.client.post(
            "/api/tasks",
            headers=self.device(),
            json={**body, "project_id": PROJECT_ID, "adapter_id": "validation"},
        )
        self.assertEqual(201, response.status_code, response.text)
        return response.json()["task"]["task_id"]

    def _paths(self, task_id: str) -> dict:
        """Criterion id to the path it names, from the resolved active set."""
        resolved = self.service.resolve_active_criteria(task_id, 1)
        return {
            item.criterion_id: item.criterion.path
            for item in resolved.active
            if item.criterion.path
        }

    def published(self, task_id: str) -> dict:
        """PR22's assessment response, unchanged."""
        response = self.client.get(self.assessment_url(task_id), headers=self.device())
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["assessment"]

    def evaluation_results(self, task_id: str) -> dict:
        """PR7's published per-criterion answers, keyed by the path each names."""
        payload = self.published(task_id)
        paths = self._paths(task_id)
        return {
            paths.get(item["criterion_id"], item["criterion_id"]): item
            for item in payload["evaluation"]["results"]
        }

    def current(self, task_id: str) -> dict:
        """PR18's per-criterion current assessment, keyed by path.

        Not routed — PR22 publishes criteria, evaluation and acceptance — so it
        is read from the service, which is where the two domains meet as
        ``turn_change`` and ``final_state``.
        """
        envelope = self.service.current_criterion_assessment(task_id, 1)
        paths = self._paths(task_id)
        return envelope, {
            paths.get(item.criterion_id, item.criterion_id): item
            for item in envelope.assessments
        }

    def test_both_domains_answer_honestly_at_the_terminal_boundary(self):
        task_id = self.create()
        baseline = self.head()

        # Dispatch returned and the worker has not run. Neither observation
        # exists, and nothing downstream invents an answer from the absence.
        early = self.published(task_id)
        self.assertEqual("not_assessable", early["acceptance"]["availability"])
        self.assertFalse((self.root / STATE_TARGET).exists())
        self.assertEqual(
            "legacy_unknown", self.service.turn_final_state(task_id, 1).state
        )
        self.assertFalse(
            self.service.store.evidence_bundle(task_id, 1).committed_range.recorded
        )

        # The worker creates one file and commits a change to the other.
        self.worker.work()
        self.assertNotEqual(baseline, self.head())
        self.service.refresh_task(task_id)

        envelope, answers = self.current(task_id)
        self.assertEqual("resolved", envelope.state)

        # PR25's domain: the file the worker left in the tree.
        self.assertEqual("met", answers[STATE_TARGET].result)
        self.assertEqual("final_state_observed", answers[STATE_TARGET].reason)
        self.assertEqual("final_state", answers[STATE_TARGET].domain)

        # PR26's domain: the change the worker committed, which the worktree
        # cannot see precisely because it was committed.
        self.assertEqual("met", answers[CHANGE_TARGET].result)
        # The binder's own reason: it carries PR7's verdict forward rather than
        # restating the evidence code, which stays PR7's to publish.
        self.assertEqual("turn_change_evaluated", answers[CHANGE_TARGET].reason)
        self.assertEqual("turn_change", answers[CHANGE_TARGET].domain)

        # PR7's published answer for the change criterion agrees.
        published = self.evaluation_results(task_id)
        self.assertEqual("met", published[CHANGE_TARGET]["result"])
        self.assertEqual(
            "machine_change_observed", published[CHANGE_TARGET]["reason"]
        )

        # And the aggregate reflects both rather than one.
        acceptance = self.published(task_id)["acceptance"]
        self.assertEqual("assessable", acceptance["availability"])
        self.assertEqual("met", acceptance["outcome"])
        self.assertEqual(2, acceptance["counts"]["met"])
        self.assertEqual(0, acceptance["counts"]["not_met"])
        self.assertEqual(0, acceptance["counts"]["unverified"])

    def test_the_committed_range_names_the_terminal_head(self):
        """The machine fact under the answer, checked against the repository."""
        task_id = self.create()
        baseline = self.head()
        self.worker.work()
        target = self.head()
        self.service.refresh_task(task_id)

        span = self.service.store.evidence_bundle(task_id, 1).committed_range
        self.assertTrue(span.recorded)
        self.assertEqual(baseline, span.baseline_revision)
        self.assertEqual(target, span.target_revision)
        self.assertEqual("linear", span.ancestry)
        self.assertEqual("complete", span.coverage)
        self.assertEqual("clean_complete", span.boundary_quality)

        # The worktree domain is genuinely closed and genuinely empty for the
        # committed path — which is why only the range could answer it.
        bundle = self.service.store.evidence_bundle(task_id, 1)
        worktree = [
            item.path
            for item in bundle.observations
            if item.domain == "worktree" and item.path
        ]
        self.assertNotIn(CHANGE_TARGET, worktree)
        committed = [
            item.path
            for item in bundle.observations
            if item.domain == "committed_range" and item.path
        ]
        self.assertEqual([CHANGE_TARGET], committed)

    def test_reading_the_assessment_again_changes_nothing(self):
        task_id = self.create()
        self.worker.work()
        self.service.refresh_task(task_id)

        first = self.published(task_id)

        # The repository moves on, and every read still answers about the turn.
        (self.root / CHANGE_TARGET).unlink()
        (self.root / STATE_TARGET).unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "somebody else, afterwards")

        for _ in range(3):
            self.assertEqual(first, self.published(task_id))
            _, answers = self.current(task_id)
            self.assertEqual("met", answers[STATE_TARGET].result)
            self.assertEqual("met", answers[CHANGE_TARGET].result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
