"""M2K PR23 — the authoring boundary over HTTP, and the whole stack behind it.

`AuthorityBoundary` is the load-bearing part. `require_task_caller` accepts two
credentials — the device token and the Actions bridge's own — so putting
`criteria` and `continuity` in a shared allowlist would have made a remote Custom
GPT user the authority on what its own work is judged against. The field list is
per caller instead, which means the bridge's request shape is byte-for-byte what
it was: an unexpected key, refused.

`CallerToAcceptance` is the other half. It drives the entire stack from the real
HTTP boundary for the first time — declare criteria and continuity, let a worker
run, read PR22's acceptance section — and shows the answer differs from an
undeclared task *because the caller declared*, not because a server guessed.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - stdlib-only CI path
    TestClient = None

DEVICE_TOKEN = "device-token-not-a-real-credential-0001"
BRIDGE_TOKEN = "bridge-internal-token-not-real-0002"
PROJECT_ID = "demo"
REPO_ROOT = Path(__file__).resolve().parents[1]

EXISTS_A = {"kind": "evidence", "predicate": "path_exists", "path": "a.txt"}
ABSENT_B = {"kind": "evidence", "predicate": "path_absent", "path": "b.txt"}
ROOT = {"mode": "root"}


class Worker:
    """Writes what the current step says, commits, and stays followup-able."""

    adapter_id = "validation"
    display_name = "Scripted"

    def __init__(self):
        self.steps = []
        self.dispatched = 0

    def capabilities(self):
        from cofferdam.workstation.tasks.adapters.protocol import AdapterCapabilities

        return AdapterCapabilities(start=True, followup=True, final_result=True)

    def available(self):
        return True

    def session_available(self, task_id):
        return True

    def _run(self, context):
        from cofferdam.workstation.tasks.adapters.protocol import AdapterOutcome

        self.dispatched += 1
        root = Path(context.project_root)
        if self.steps:
            self.steps.pop(0)(root)
            subprocess.run(("git", "add", "-A"), cwd=root, check=True,
                           capture_output=True)
            subprocess.run(
                ("git", "-c", "user.email=w@example.invalid", "-c",
                 "user.name=Worker", "commit", "-qm", "worker"),
                cwd=root, check=False, capture_output=True,
            )
        return AdapterOutcome(requested_state="ready_for_followup", final_result="done")

    def start(self, context):
        return self._run(context)

    def send_followup(self, context, followup):
        return self._run(context)


@unittest.skipIf(TestClient is None, "workstation extras are not installed")
class AuthoringApiCase(unittest.TestCase):
    def setUp(self) -> None:
        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.project_root = self.home / "projects" / PROJECT_ID
        self.project_root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        (self.project_root / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

        config = load_config(self.home)
        config = type(config)(
            **{**config.__dict__, "enable_validation_task_adapter": True,
               "enable_actions_bridge_caller": True}
        )
        config.ensure_dirs()
        (config.config_dir / "task-projects.json").write_text(
            json.dumps({"projects": [{
                "project_id": PROJECT_ID, "display_name": "Demo",
                "root": str(self.project_root), "adapters": ["validation"],
                "enabled": True,
            }]}),
            encoding="utf-8",
        )
        bridge_path = config.actions_bridge_token_path
        bridge_path.write_text(BRIDGE_TOKEN + "\n", encoding="utf-8")
        bridge_path.chmod(0o600)

        self.config = config
        self.database = self.home / "state" / "tasks" / "tasks.sqlite3"
        self.app = create_app(
            config=config, token=DEVICE_TOKEN, adapter=StubAdapter(config)
        )
        self.client = TestClient(self.app)
        self.service = self.app.state.tasks

        self.worker = Worker()
        from cofferdam.workstation.tasks import build_registry

        registry = type(build_registry(enable_validation_adapter=True))((self.worker,))
        self.service._adapters = registry

    def git(self, *arguments):
        subprocess.run(("git",) + arguments, cwd=self.project_root, check=True,
                       capture_output=True)

    def device(self):
        return {"Authorization": "Bearer " + DEVICE_TOKEN}

    def bridge(self):
        return {"Authorization": "Bearer " + BRIDGE_TOKEN}

    def create(self, headers=None, **fields):
        body = {"project_id": PROJECT_ID, "adapter_id": "validation",
                "prompt": "do the work"}
        body.update(fields)
        return self.client.post(
            "/api/tasks", headers=headers or self.device(), json=body
        )

    def followup(self, task_id, headers=None, **fields):
        body = {"followup": "more"}
        body.update(fields)
        return self.client.post(
            "/api/tasks/%s/followups" % task_id, headers=headers or self.device(),
            json=body,
        )

    def acceptance(self, task_id, turn=1):
        response = self.client.get(
            "/api/tasks/%s/turns/%s/assessment" % (task_id, turn),
            headers=self.device(),
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["assessment"]["acceptance"]

    def lineage(self, task_id, turn):
        return self.service.store.turn_continuity(task_id, turn)

    def snap(self, task_id, turn):
        return self.service.store.turn_criteria(task_id, turn).snapshot_id


class AuthorityBoundary(AuthoringApiCase):
    """Who may declare. The one thing this PR could most easily get wrong."""

    def test_the_device_caller_may_declare(self):
        response = self.create(criteria=[EXISTS_A], continuity=ROOT)
        self.assertEqual(201, response.status_code, response.text)

    def test_the_bridge_credential_may_not_declare_continuity(self):
        response = self.create(headers=self.bridge(), continuity=ROOT)
        self.assertEqual(422, response.status_code)

    def test_the_bridge_credential_may_not_declare_criteria(self):
        response = self.create(headers=self.bridge(), criteria=[EXISTS_A])
        self.assertEqual(422, response.status_code)

    def test_the_bridge_may_still_create_an_ordinary_task(self):
        """Its contract is unchanged, not narrowed."""
        self.assertEqual(201, self.create(headers=self.bridge()).status_code)

    def test_the_bridge_may_not_declare_on_a_follow_up_either(self):
        created = self.create(headers=self.bridge())
        task_id = created.json()["task"]["task_id"]
        response = self.followup(task_id, headers=self.bridge(), continuity=ROOT)
        self.assertEqual(422, response.status_code)

    def test_a_refused_bridge_declaration_creates_nothing(self):
        before = len(self.service.store.list_tasks())
        self.create(headers=self.bridge(), continuity=ROOT)
        self.assertEqual(before, len(self.service.store.list_tasks()))

    def test_an_unauthenticated_caller_may_not_declare(self):
        response = self.client.post(
            "/api/tasks",
            json={"project_id": PROJECT_ID, "adapter_id": "validation",
                  "prompt": "x", "continuity": ROOT},
        )
        self.assertEqual(401, response.status_code)

    def test_the_gate_is_derived_from_the_credential_not_the_body(self):
        """There is no field anywhere for a caller to name itself."""
        source = (
            REPO_ROOT / "cofferdam" / "workstation" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_authoring_fields", source)
        self.assertNotIn('payload.get("caller")', source)
        self.assertNotIn('payload.get("origin")', source)


class BridgeNegativeSpace(AuthoringApiCase):
    def test_the_bridge_package_names_no_authoring_field(self):
        base = REPO_ROOT / "cofferdam" / "actions_bridge"
        if not base.exists():  # pragma: no cover - layout guard
            self.skipTest("no bridge package")
        for path in sorted(base.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for forbidden in ('"criteria"', '"continuity"', '"supersedes"',
                              "predecessor_snapshot_id", "criterion_ordinal"):
                self.assertNotIn(forbidden, text, str(path))

    def test_no_bridge_schema_exposes_a_declaration(self):
        base = REPO_ROOT / "cofferdam" / "actions_bridge"
        if not base.exists():  # pragma: no cover - layout guard
            self.skipTest("no bridge package")
        for path in sorted(base.rglob("*.json")) + sorted(base.rglob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("criteria", "continuity", "supersedes"):
                self.assertNotIn(forbidden, text, str(path))


class NoHiddenDefaultsOverHttp(AuthoringApiCase):
    def test_an_omitted_declaration_stays_not_declared(self):
        task_id = self.create().json()["task"]["task_id"]
        self.assertEqual("not_declared", self.lineage(task_id, 1).state)

    def test_an_explicit_root_is_declared(self):
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        declaration = self.lineage(task_id, 1)
        self.assertEqual("declared", declaration.state)
        self.assertEqual("root", declaration.mode)

    def test_an_omitted_follow_up_declaration_stays_not_declared(self):
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        self.assertEqual(200, self.followup(task_id, criteria=[ABSENT_B]).status_code)
        self.assertEqual("not_declared", self.lineage(task_id, 2).state)

    def test_an_explicit_follow_up_extend_is_declared(self):
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        self.followup(
            task_id, criteria=[ABSENT_B],
            continuity={"mode": "extend",
                        "predecessor_snapshot_id": self.snap(task_id, 1)},
        )
        self.assertEqual("extend", self.lineage(task_id, 2).mode)


class OldClientCompatibility(AuthoringApiCase):
    def test_a_payload_without_the_new_fields_still_works(self):
        self.assertEqual(201, self.create().status_code)

    def test_a_follow_up_without_them_still_works(self):
        task_id = self.create().json()["task"]["task_id"]
        self.assertEqual(200, self.followup(task_id).status_code)

    def test_behaviour_for_such_a_payload_is_exactly_what_it_was(self):
        task_id = self.create().json()["task"]["task_id"]
        self.assertEqual("not_declared", self.lineage(task_id, 1).state)
        self.assertEqual("not_provided", self.service.store.turn_criteria(task_id, 1).state)

    def test_an_unknown_field_is_still_refused(self):
        response = self.create(nonsense={"a": 1})
        self.assertEqual(422, response.status_code)


class InvalidDeclarationOverHttp(AuthoringApiCase):
    def test_an_unknown_mode_is_a_bounded_client_error(self):
        response = self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})
        self.assertEqual(422, response.status_code)
        body = response.json()
        self.assertEqual("task_continuity_invalid", body["error"]["code"])

    def test_invalid_criteria_are_a_bounded_client_error(self):
        response = self.create(
            criteria=[{"kind": "evidence", "predicate": "nope", "path": "a.py"}]
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("task_criteria_invalid", response.json()["error"]["code"])

    def test_a_stale_supersession_reads_as_invalid_not_unrecorded(self):
        """The translation debt, over the wire."""
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        response = self.followup(
            task_id, criteria=[ABSENT_B],
            continuity={
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(task_id, 1),
                "supersedes": [{"criterion_ordinal": 1,
                                "predecessor_criterion_id": "criterion_" + "0" * 16}],
            },
        )
        self.assertEqual(422, response.status_code)
        code = response.json()["error"]["code"]
        self.assertEqual("task_continuity_invalid", code)
        self.assertNotEqual("task_continuity_unrecorded", code)

    def test_a_refusal_leaks_no_host_detail(self):
        response = self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})
        text = response.text
        for forbidden in ("Traceback", "sqlite", str(self.home), "/home/", 'File "'):
            self.assertNotIn(forbidden, text, forbidden)

    def test_no_worker_ran_for_any_refused_request(self):
        before = self.worker.dispatched
        self.create(criteria=[EXISTS_A], continuity={"mode": "preserve"})
        self.create(criteria=[{"kind": "evidence", "predicate": "nope", "path": "a"}])
        self.assertEqual(before, self.worker.dispatched)


class CallerToAcceptance(AuthoringApiCase):
    """The first time the whole stack is driven from a real caller boundary."""

    def test_declaring_makes_a_turn_assessable(self):
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8")
        ]
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        acceptance = self.acceptance(task_id)
        self.assertEqual("assessable", acceptance["availability"])
        self.assertEqual("met", acceptance["outcome"])
        self.assertEqual({"total": 1, "met": 1, "not_met": 0, "unverified": 0},
                         acceptance["counts"])
        self.assertIs(False, acceptance["requires_human"])

    def test_the_same_request_without_a_declaration_is_not_assessable(self):
        """The difference is the caller's declaration, not a server guess."""
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8")
        ]
        task_id = self.create(criteria=[EXISTS_A]).json()["task"]["task_id"]
        acceptance = self.acceptance(task_id)
        self.assertEqual("not_assessable", acceptance["availability"])
        self.assertEqual("continuity_not_declared", acceptance["availability_reason"])
        self.assertIsNone(acceptance["counts"])

    def test_a_declared_turn_can_be_not_met(self):
        self.worker.steps = [lambda root: None]
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        acceptance = self.acceptance(task_id)
        self.assertEqual("assessable", acceptance["availability"])
        self.assertEqual("not_met", acceptance["outcome"])

    def test_an_explicit_extend_carries_the_active_set_forward(self):
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8"),
            lambda root: None,
        ]
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        self.followup(
            task_id, criteria=[ABSENT_B],
            continuity={"mode": "extend",
                        "predecessor_snapshot_id": self.snap(task_id, 1)},
        )
        acceptance = self.acceptance(task_id, 2)
        self.assertEqual("assessable", acceptance["availability"])
        self.assertEqual(2, acceptance["counts"]["total"])
        self.assertEqual("met", acceptance["outcome"])

    def test_an_explicit_replace_cuts_the_old_set(self):
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8"),
            lambda root: None,
        ]
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        self.followup(
            task_id, criteria=[ABSENT_B],
            continuity={"mode": "replace",
                        "predecessor_snapshot_id": self.snap(task_id, 1)},
        )
        self.assertEqual(1, self.acceptance(task_id, 2)["counts"]["total"])

    def test_an_explicit_revise_supersedes_an_inherited_criterion(self):
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8"),
            lambda root: None,
        ]
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        retired = self.service.store.turn_criteria(task_id, 1).criteria[0].criterion_id
        self.followup(
            task_id, criteria=[ABSENT_B],
            continuity={
                "mode": "revise",
                "predecessor_snapshot_id": self.snap(task_id, 1),
                "supersedes": [{"criterion_ordinal": 1,
                                "predecessor_criterion_id": retired}],
            },
        )
        self.assertEqual(1, self.acceptance(task_id, 2)["counts"]["total"])

    def test_an_explicit_declaration_with_no_criteria_is_no_structured_criteria(self):
        """Distinct from an omitted declaration, and it must stay distinct."""
        task_id = self.create(criteria=[], continuity=ROOT).json()["task"]["task_id"]
        acceptance = self.acceptance(task_id)
        self.assertEqual("not_assessable", acceptance["availability"])
        self.assertEqual("no_structured_criteria", acceptance["availability_reason"])
        self.assertEqual({"total": 0, "met": 0, "not_met": 0, "unverified": 0},
                         acceptance["counts"])
        self.assertIs(False, acceptance["requires_human"])

    def test_a_manual_criterion_still_wants_a_person(self):
        self.worker.steps = [
            lambda root: (root / "a.txt").write_text("made\n", encoding="utf-8")
        ]
        task_id = self.create(
            criteria=[EXISTS_A, {"kind": "manual", "description": "somebody looks"}],
            continuity=ROOT,
        ).json()["task"]["task_id"]
        acceptance = self.acceptance(task_id)
        self.assertEqual("incomplete", acceptance["outcome"])
        self.assertIs(True, acceptance["requires_human"])

    def test_a_manual_criterion_is_orthogonal_to_a_not_met_one(self):
        """The outcome is decided, and a person is still needed."""
        self.worker.steps = [lambda root: None]
        task_id = self.create(
            criteria=[EXISTS_A, {"kind": "manual", "description": "somebody looks"}],
            continuity=ROOT,
        ).json()["task"]["task_id"]
        acceptance = self.acceptance(task_id)
        self.assertEqual("not_met", acceptance["outcome"])
        self.assertIs(True, acceptance["requires_human"])

    def test_the_acceptance_read_is_still_device_only(self):
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        response = self.client.get(
            "/api/tasks/%s/turns/1/assessment" % task_id, headers=self.bridge()
        )
        self.assertEqual(401, response.status_code)

    def test_repeated_reads_still_mutate_nothing(self):
        task_id = self.create(criteria=[EXISTS_A], continuity=ROOT).json()["task"]["task_id"]
        self.acceptance(task_id)
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        before = connection.execute(
            "SELECT count(*) FROM task_turn_criteria_continuity"
        ).fetchone()[0]
        connection.close()
        for _ in range(10):
            self.acceptance(task_id)
        connection = sqlite3.connect("file:%s?mode=ro" % self.database, uri=True)
        after = connection.execute(
            "SELECT count(*) FROM task_turn_criteria_continuity"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
