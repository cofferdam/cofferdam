"""M2K PR3 — one end-to-end pass over a real Git repository.

An isolated ``COFFERDAM_HOME`` and a real repository built by real ``git``. No
provider, no model, no network. The unit tests each pin one rule; this walks the
whole path — Git state, observation, event, bundle — and asserts the properties
that only appear when every layer runs in order against output nobody here wrote.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.adapters.claude_code.evidence import (
    git_evidence,
    observe_git,
)
from cofferdam.workstation.tasks.claims import (
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    ClaimSubmission,
)
from cofferdam.workstation.tasks.evidence import (
    OPERATION_AGREED,
    OPERATION_DIFFERS,
    OPERATION_UNKNOWN,
    RELATIONSHIP_CLAIM_CONFLICT,
    RELATIONSHIP_CLAIM_ONLY,
    RELATIONSHIP_PATH_AGREED,
)
from cofferdam.workstation.tasks.models import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
)
from cofferdam.workstation.tasks.store import SCHEMA_VERSION, TaskStore, _TurnClose

GIT = shutil.which("git")

_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.st",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.st",
}


@unittest.skipIf(GIT is None, "git is not installed")
class GitEndToEnd(unittest.TestCase):
    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-pr3-e2e-")
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)
        self.root = self.home / "synthetic-project"
        self.root.mkdir()
        self.git("init", "-q", ".")
        self.write("keep.txt", "one\n")
        self.write("editme.txt", "two\n")
        self.write("deleteme.txt", "three\n")
        self.write("moveme.txt", "four\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

        config = load_config(self.home)
        config.ensure_dirs()
        self.config = config
        self.store = TaskStore(config)
        self.addCleanup(self._close)
        self.task_id = self._task()

    def _close(self):
        try:
            self.store.close()
        except Exception:
            pass

    def git(self, *args):
        subprocess.run(
            [GIT, *args], cwd=str(self.root), check=True, capture_output=True,
            env={**_ENV, "HOME": str(self.root)},
        )

    def write(self, relative, body="x\n"):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def _task(self):
        row, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="synthetic",
            prompt="change things", title="e2e",
        )
        for state in ("queued", "starting", "running"):
            self.store.transition(
                row.task_id, state, event_type="task_" + state,
                actor="system", source="cofferdam",
            )
        self.store.open_turn(
            row.task_id, provider="validation", source="internal_test",
            started_at="2026-08-14T00:00:00Z",
        )
        return row.task_id

    def observe_and_record(self):
        """The real adapter path: run Git, emit references, append one event."""
        observation = observe_git(self.root)
        references = git_evidence(observation)
        self.store.append_event(
            self.task_id, "progress", actor="system", source="cofferdam",
            text="Cofferdam checked the project itself.", evidence=references,
        )
        return observation, references

    def claim(self, *submissions):
        return self.store.record_change_claims(
            self.task_id, submissions, project_root=self.root, turn_number=1
        )

    def bundle(self):
        self.store.transition(
            self.task_id, "ready_for_followup", event_type="turn_complete",
            actor="adapter", source="adapter",
            close_turn=_TurnClose(outcome="completed", completed_at="2026-08-14T00:05:00Z"),
        )
        return self.store.evidence_bundle(self.task_id, 1)

    # -- the walk ---------------------------------------------------------

    def test_the_whole_path(self):
        # 1. Schema is untouched by PR3. PR4 later took it to 6 by adding the
        #    pre-work baseline table, which changes nothing on this path.
        self.assertEqual(SCHEMA_VERSION, 6)
        self.assertEqual(self.store.storage_health()["schema_version"], 6)

        # 3/7/9/11. Real Git changes of each kind.
        self.write("editme.txt", "two\nedited\n")     # modified
        self.write("newfile.txt", "brand new\n")      # created (untracked)
        self.git("rm", "-q", "deleteme.txt")          # deleted
        self.git("mv", "moveme.txt", "moved.txt")     # renamed

        observation, references = self.observe_and_record()
        kinds = {c.path: c.kind for c in observation.changes}

        # 4/8/10. The machine recorded what happened, not just that it happened.
        self.assertEqual(kinds["editme.txt"], CHANGE_MODIFIED)
        self.assertEqual(kinds["newfile.txt"], CHANGE_CREATED)
        self.assertEqual(kinds["deleteme.txt"], CHANGE_DELETED)
        self.assertEqual(kinds["moved.txt"], CHANGE_RENAMED)

        # 11. Both rename paths survived, the right way round.
        rename = next(c for c in observation.changes if c.kind == CHANGE_RENAMED)
        self.assertEqual(rename.path, "moved.txt")
        self.assertEqual(rename.previous_path, "moveme.txt")
        self.assertNotIn("moveme.txt", kinds)

        # 5/12. Claims, including an exact rename and one deliberate mismatch.
        self.claim(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="editme.txt"),
            ClaimSubmission(operation=CLAIM_CREATED, path="newfile.txt"),
            ClaimSubmission(operation=CLAIM_DELETED, path="deleteme.txt"),
            ClaimSubmission(operation=CLAIM_RENAMED, path="moveme.txt", to_path="moved.txt"),
            # 13. Says modified; Git says deleted. Explicitly incompatible.
            ClaimSubmission(operation=CLAIM_MODIFIED, path="deleteme.txt"),
            # 14. Claimed, never observed. Absence, not conflict.
            ClaimSubmission(operation=CLAIM_MODIFIED, path="untouched.txt"),
        )

        bundle = self.bundle()
        groups = {g.path: g for g in bundle.relationships}

        # 6. Operation agreement is now answerable.
        self.assertEqual(groups["editme.txt"].operation_agreement, OPERATION_AGREED)
        self.assertEqual(groups["newfile.txt"].operation_agreement, OPERATION_AGREED)
        self.assertEqual(groups["editme.txt"].relationship, RELATIONSHIP_PATH_AGREED)

        # 12. The exact rename agrees, because both sides matched.
        self.assertEqual(groups["moved.txt"].operation_agreement, OPERATION_AGREED)

        # 13. The incompatible pair is a conflict — one path claimed both
        #     modified and deleted, and Git said deleted.
        self.assertEqual(
            groups["deleteme.txt"].operation_agreement, OPERATION_DIFFERS
        )
        self.assertEqual(
            groups["deleteme.txt"].relationship, RELATIONSHIP_CLAIM_CONFLICT
        )
        self.assertTrue(groups["deleteme.txt"].path_agreement)

        # 14. Absence is not conflict.
        self.assertEqual(groups["untouched.txt"].relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertEqual(
            groups["untouched.txt"].operation_agreement, OPERATION_UNKNOWN
        )

        # 16. Observation completeness is visible and, here, complete.
        self.assertTrue(bundle.machine_observations_complete)

        # 18. Provenance is untouched by any of it.
        published = bundle.to_dict()
        for claim in published["claims"]:
            self.assertEqual(claim["source"], "adapter_reported")
            self.assertFalse(claim["verified"])
        for machine in published["observations"]:
            self.assertEqual(machine["source"], "git_observed")
            self.assertTrue(machine["verified"])

        # 19/20/21/22. Nothing beyond evidence exists on this path.
        blob = json.dumps(published).lower()
        for forbidden in ("verdict", "confidence", "risk", "check_runner", "provider", "score"):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_a_legacy_shaped_observation_still_reads_as_unknown(self):
        """15. Old evidence beside new evidence, in one turn."""
        from cofferdam.workstation.tasks.models import (
            EVIDENCE_FILE,
            EVIDENCE_GIT_OBSERVED,
            EvidenceReference,
        )

        self.write("editme.txt", "two\nedited\n")
        self.store.append_event(
            self.task_id, "progress", actor="system", source="cofferdam",
            text="an older build looked",
            evidence=(
                EvidenceReference(
                    evidence_type=EVIDENCE_FILE, source=EVIDENCE_GIT_OBSERVED,
                    identifier="editme.txt", operation="git status", result="changed",
                ),
            ),
        )
        self.claim(ClaimSubmission(operation=CLAIM_DELETED, path="editme.txt"))
        group = {g.path: g for g in self.bundle().relationships}["editme.txt"]
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertEqual(group.relationship, RELATIONSHIP_PATH_AGREED)
        self.assertEqual(group.observed_kinds, ())

    def test_a_wholly_new_directory_is_enumerated_file_by_file(self):
        """This used to collapse to one `?? bulk/` record. It no longer does.

        `--untracked-files=all` makes the observation file-level, which is the
        granularity a `ChangeClaim` is written at — a directory record could
        never pair with a claim about `bulk/f00.txt`.
        """
        for index in range(3):
            self.write("bulk/f%02d.txt" % index)
        observation, _ = self.observe_and_record()
        paths = {c.path for c in observation.changes}
        self.assertEqual(paths, {"bulk/f00.txt", "bulk/f01.txt", "bulk/f02.txt"})
        self.assertNotIn("bulk/", paths)
        self.assertEqual(observation.refused_count, 0)
        self.assertTrue(observation.complete)

    def test_a_truncated_observation_is_visible(self):
        """16. More changed paths than the budget, and the bundle says so.

        Tracked files, modified — Git lists those individually, where an
        untracked directory would collapse to one record.
        """
        for index in range(30):
            self.write("bulk/f%02d.txt" % index)
        self.git("add", "-A")
        self.git("commit", "-qm", "bulk")
        for index in range(30):
            self.write("bulk/f%02d.txt" % index, "edited %d\n" % index)
        observation, _ = self.observe_and_record()
        self.assertGreater(len(observation.changes), 6)
        bundle = self.bundle()
        self.assertFalse(bundle.machine_observations_complete)
        self.assertIn("machine_observations_incomplete", bundle.limitations)

    def test_committed_work_is_invisible_and_that_is_stated_not_hidden(self):
        """The coverage limit the audit found, asserted rather than described.

        A worker that commits its changes leaves a clean tree, and `git status`
        reports nothing. The bundle must not present that as "the worker changed
        nothing" — it reports a clean observation and no path evidence, and the
        claim stays `claim_only` rather than becoming a conflict.
        """
        self.write("editme.txt", "two\nedited\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "worker committed its own work")

        observation, _ = self.observe_and_record()
        self.assertEqual(observation.changes, ())
        self.assertTrue(observation.clean)

        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="editme.txt"))
        bundle = self.bundle()
        group = {g.path: g for g in bundle.relationships}["editme.txt"]
        self.assertEqual(group.relationship, RELATIONSHIP_CLAIM_ONLY)
        self.assertNotEqual(group.relationship, RELATIONSHIP_CLAIM_CONFLICT)
        self.assertEqual(group.operation_agreement, OPERATION_UNKNOWN)
        self.assertTrue(bundle.repository_reported_clean)

    def test_the_assembler_never_runs_git_itself(self):
        """The whole repository is deleted after the evidence is stored."""
        self.write("editme.txt", "two\nedited\n")
        self.observe_and_record()
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="editme.txt"))
        before = self.bundle().to_dict()

        shutil.rmtree(self.root)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.store.evidence_bundle(self.task_id, 1).to_dict(), before)

    def test_turn_isolation_survives_the_richer_observations(self):
        """17. Turn 2's machine evidence cannot reach turn 1's bundle."""
        self.write("editme.txt", "two\nedited\n")
        self.observe_and_record()
        self.claim(ClaimSubmission(operation=CLAIM_MODIFIED, path="editme.txt"))
        first = self.bundle()
        fingerprint = first.input_fingerprint

        from cofferdam.workstation.tasks.store import _TurnDraft

        self.store.transition(
            self.task_id, "running", event_type="followup_received", actor="user",
            source="cofferdam",
            open_turn=_TurnDraft(
                provider="validation", source="internal_test",
                started_at="2026-08-14T00:06:00Z",
            ),
        )
        self.write("newfile.txt", "second turn\n")
        self.observe_and_record()

        again = self.store.evidence_bundle(self.task_id, 1)
        self.assertEqual(again.input_fingerprint, fingerprint)
        self.assertEqual(again.to_dict(), first.to_dict())
        self.assertNotIn("newfile.txt", [o.path for o in again.observations])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
