"""Proposal → acceptance → hash-bound atomic apply, and every way it must refuse.

D-2026-08-11-4 is the specification: no model ever silently writes durable
memory; a proposal touches nothing; a person accepts; the apply is atomic and
**refuses when the base hash no longer matches**; deletion is never proposable.

The cases that matter are the ones where a write happens when it should not:

* creating a proposal writing Markdown;
* an accepted proposal landing on a file that changed since it was reviewed;
* a rejected or already-applied proposal being replayed;
* an apply touching more than the single approved target.

Every assertion about "nothing was written" compares real bytes on a real
filesystem before and after, not a flag.
"""

from __future__ import annotations

import os
import stat
import unittest

from ._mind_doubles import MindHarness


class Creation(MindHarness):
    grant_vault = True

    def test_creating_a_proposal_writes_no_markdown(self):
        self.activate()
        before = self.snapshot()
        self.mind.create_proposal(
            scope="project", role="status", content="# Status\n\nrewritten\n", reason="tidy"
        )
        self.assertEqual(self.snapshot(), before)

    def test_a_proposal_records_the_base_hash_of_the_current_file(self):
        from cofferdam.workstation.mind.hashing import document_hash

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="tidy"
        )
        expected = document_hash((self.project_root / "STATUS.md").read_bytes())
        self.assertEqual(created["base_hash"], expected)
        self.assertEqual(created["state"], "pending")

    def test_a_proposal_is_minted_by_cofferdam(self):
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="tidy"
        )
        self.assertTrue(created["proposal_id"].startswith("mprop_"))
        self.assertTrue(created["created_at"])

    def test_the_source_is_assigned_and_never_supplied(self):
        import inspect

        from cofferdam.workstation.mind.service import MindService

        signature = inspect.signature(MindService.create_proposal)
        self.assertIn("source", signature.parameters)
        self.assertEqual(signature.parameters["source"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="tidy"
        )
        self.assertEqual(created["source"], "user")

    def test_a_proposal_against_an_unmapped_role_is_refused(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.create_proposal(
                scope="project", role="decisions", content="x", reason="y"
            )
        self.assertEqual(caught.exception.code, "mind_role_unconfigured")

    def test_a_proposal_against_a_missing_file_is_refused(self):
        """PR2 modifies approved documents. It does not create them."""
        from cofferdam.workstation.mind.errors import MindError

        (self.project_root / "STATUS.md").unlink()
        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.create_proposal(
                scope="project", role="status", content="x", reason="y"
            )
        self.assertEqual(caught.exception.code, "mind_role_unavailable")
        self.assertFalse((self.project_root / "STATUS.md").exists())

    def test_empty_content_is_refused(self):
        """An empty document is a deletion wearing a mutation's clothes."""
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        for empty in ("", "   \n  ", None, 42, b"bytes"):
            with self.subTest(content=empty):
                with self.assertRaises(MindError) as caught:
                    self.mind.create_proposal(
                        scope="project", role="status", content=empty, reason="y"
                    )
                self.assertEqual(caught.exception.code, "mind_content_invalid")

    def test_oversized_content_is_refused(self):
        from cofferdam.workstation.mind.documents import MAX_DOCUMENT_BYTES
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.create_proposal(
                scope="project",
                role="status",
                content="x" * (MAX_DOCUMENT_BYTES + 1),
                reason="y",
            )
        self.assertEqual(caught.exception.code, "mind_content_invalid")

    def test_a_reason_is_required_and_bounded(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        for reason in ("", None, "r" * 1000):
            with self.subTest(reason=reason):
                with self.assertRaises(MindError) as caught:
                    self.mind.create_proposal(
                        scope="project", role="status", content="new\n", reason=reason
                    )
                self.assertEqual(caught.exception.code, "mind_reason_invalid")


class HashBoundApply(MindHarness):
    grant_vault = True

    def create(self, *, scope="project", role="status", content="# Status\n\nrewritten\n"):
        return self.mind.create_proposal(
            scope=scope, role=role, content=content, reason="record the new state"
        )

    def test_acceptance_applies_the_reviewed_content(self):
        self.activate()
        created = self.create()
        result = self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(result["state"], "applied")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\nrewritten\n")

    def test_acceptance_changes_exactly_one_file(self):
        self.activate()
        before = self.snapshot()
        created = self.create()
        self.mind.accept_proposal(created["proposal_id"])
        after = self.snapshot()

        self.assertEqual(set(before), set(after))
        changed = [key for key in before if before[key] != after[key]]
        self.assertEqual(changed, [str(self.project_root / "STATUS.md")])

    def test_a_target_changed_after_the_proposal_refuses_as_stale(self):
        self.activate()
        created = self.create()

        # Somebody edits the file in a text editor, which is the whole point of
        # Markdown being canonical.
        (self.project_root / "STATUS.md").write_text("# Status\n\nby hand\n", encoding="utf-8")

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_stale")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\nby hand\n")

    def test_a_stale_proposal_is_recorded_as_stale_and_stays_that_way(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create()
        (self.project_root / "STATUS.md").write_text("drifted\n", encoding="utf-8")
        with self.assertRaises(MindError):
            self.mind.accept_proposal(created["proposal_id"])

        stored = self.mind.get_proposal(created["proposal_id"])
        self.assertEqual(stored["state"], "stale")

        # Putting the original content back does not resurrect it.
        (self.project_root / "STATUS.md").write_text("# Status\n\noriginal\n", encoding="utf-8")
        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_not_pending")

    def test_a_target_that_vanished_refuses_as_stale(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create()
        (self.project_root / "STATUS.md").unlink()
        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_stale")
        self.assertFalse((self.project_root / "STATUS.md").exists())

    def test_an_applied_proposal_cannot_be_replayed(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create()
        self.mind.accept_proposal(created["proposal_id"])
        (self.project_root / "STATUS.md").write_text("later work\n", encoding="utf-8")

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_not_pending")
        self.assertEqual(self.project_text("STATUS.md"), "later work\n")

    def test_a_rejected_proposal_cannot_be_applied(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create()
        rejected = self.mind.reject_proposal(created["proposal_id"])
        self.assertEqual(rejected["state"], "rejected")

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_not_pending")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\noriginal\n")

    def test_rejection_writes_nothing(self):
        self.activate()
        created = self.create()
        before = self.snapshot()
        self.mind.reject_proposal(created["proposal_id"])
        self.assertEqual(self.snapshot(), before)

    def test_rejecting_an_applied_proposal_is_refused(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create()
        self.mind.accept_proposal(created["proposal_id"])
        with self.assertRaises(MindError) as caught:
            self.mind.reject_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_not_pending")

    def test_an_unknown_proposal_is_refused(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        for candidate in ("mprop_0000000000000000000000000", "nonsense", "", None):
            with self.subTest(proposal_id=candidate):
                with self.assertRaises(MindError) as caught:
                    self.mind.accept_proposal(candidate)
                self.assertEqual(caught.exception.code, "mind_proposal_unknown")

    def test_a_workspace_switch_cannot_retarget_a_pending_proposal(self):
        """The proposal remembers which workspace it was made in."""
        import json

        from cofferdam.workstation.mind.errors import MindError

        second_root = self.home / "projects" / "other"
        second_root.mkdir(parents=True)
        (second_root / "STATUS.md").write_text("other project\n", encoding="utf-8")
        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {"project_id": "demo", "root": str(self.project_root),
                         "adapters": ["validation"]},
                        {"project_id": "other", "root": str(second_root),
                         "adapters": ["validation"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.config.config_dir / "workspaces.json").write_text(
            json.dumps(
                {
                    "workspaces": [
                        {"workspace_id": "demo-workspace", "project_id": "demo",
                         "documents": {"status": "STATUS.md"}},
                        {"workspace_id": "other-workspace", "project_id": "other",
                         "documents": {"status": "STATUS.md"}},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.build_services()
        self.activate()
        created = self.create()

        self.workspaces.reload_workspaces()
        self.workspaces.activate("other-workspace")

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_workspace_changed")
        self.assertEqual((second_root / "STATUS.md").read_text(encoding="utf-8"),
                         "other project\n")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\noriginal\n")

    def test_a_revoked_grant_stops_a_pending_global_proposal(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create(scope="global", role="user", content="# User\n\nnew\n")
        self.remove_grant()
        self.build_services()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_global_grant_missing")
        self.assertEqual(self.vault_text("USER.md"), "# User\n\noriginal\n")

    def test_a_remapped_role_makes_the_proposal_stale_rather_than_moving_it(self):
        """The apply resolves the role again, and the new file is not the base."""
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.create()
        self.write_workspaces(documents={"status": "ROADMAP.md", "plan": "ROADMAP.md"})
        self.build_services()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_proposal_stale")
        self.assertEqual(self.project_text("ROADMAP.md"), "# Roadmap\n\noriginal\n")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\noriginal\n")


class NoDeletion(MindHarness):
    """There is no operation word for removing memory, so there is no path to it."""

    grant_vault = True

    def test_the_operation_vocabulary_has_exactly_one_word(self):
        from cofferdam.workstation.mind.store import OPERATIONS, OPERATION_REPLACE

        self.assertEqual(OPERATIONS, (OPERATION_REPLACE,))

    def test_no_delete_rename_or_move_verb_exists_on_the_service(self):
        from cofferdam.workstation.mind.service import MindService

        for verb in ("delete", "remove", "rename", "move", "unlink", "mkdir", "create_file"):
            for name in dir(MindService):
                self.assertNotIn(verb, name, "MindService." + name + " looks like " + verb)

    def test_applying_never_removes_a_file(self):
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="still here\n", reason="y"
        )
        self.mind.accept_proposal(created["proposal_id"])
        for name in ("STATUS.md", "ROADMAP.md", "UNRELATED.md"):
            self.assertTrue((self.project_root / name).exists(), name)


class AtomicWrite(MindHarness):
    grant_vault = True

    def test_the_target_keeps_its_permissions(self):
        self.activate()
        target = self.project_root / "STATUS.md"
        os.chmod(target, 0o640)
        before = stat.S_IMODE(os.stat(target).st_mode)

        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), before)

    def test_no_temporary_file_survives_a_successful_apply(self):
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        self.mind.accept_proposal(created["proposal_id"])
        leftovers = [p.name for p in self.project_root.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_no_temporary_file_survives_a_failed_apply(self):
        """The replace fails; the directory is left exactly as it was."""
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )

        original = os.replace

        def refuse(src, dst):
            raise OSError("no")

        documents_module.os.replace = refuse
        try:
            with self.assertRaises(MindError) as caught:
                self.mind.accept_proposal(created["proposal_id"])
        finally:
            documents_module.os.replace = original

        self.assertEqual(caught.exception.code, "mind_apply_failed")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\noriginal\n")
        leftovers = [p.name for p in self.project_root.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])
        self.assertEqual(self.mind.get_proposal(created["proposal_id"])["state"], "pending")

    def test_nothing_in_the_whole_path_opens_a_socket(self):
        """Reading and changing memory is local. There is no egress here at all.

        Asserted by sabotage rather than by reading imports, so it also covers
        anything reached indirectly. `CloudContextProjection` does not exist yet
        (D-2026-08-11-5), and this is what "does not exist yet" looks like from
        the outside.
        """
        import socket

        class Refused(socket.socket):  # pragma: no cover - constructing it fails
            def __init__(self, *args, **kwargs):
                raise AssertionError("the mind path opened a socket")

        self.activate()
        original = socket.socket
        socket.socket = Refused
        try:
            self.mind.available()
            self.mind.read_document("project", "status")
            self.mind.read_document("global", "user")
            created = self.mind.create_proposal(
                scope="project", role="status", content="local only\n", reason="y"
            )
            self.mind.accept_proposal(created["proposal_id"])
        finally:
            socket.socket = original

        self.assertEqual(self.project_text("STATUS.md"), "local only\n")

    def test_a_store_failure_during_repair_does_not_mask_the_write_failure(self):
        """The caller must hear about the disk, not about the database.

        The apply claims `applied` before it writes, so a failed write has to put
        the row back. If that repair itself raises, the original `mind_apply_failed`
        must still be what reaches the caller — otherwise they are told the wrong
        thing about the wrong subsystem, while the row stays `applied`, which is
        the state the repair existed to undo.
        """
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )

        original_replace = os.replace

        def refuse(src, dst):
            raise OSError("no")

        def also_broken(_proposal_id):
            raise RuntimeError("the store is gone too")

        documents_module.os.replace = refuse
        self.mind_store.reopen = also_broken
        try:
            with self.assertRaises(MindError) as caught:
                self.mind.accept_proposal(created["proposal_id"])
        finally:
            documents_module.os.replace = original_replace

        self.assertEqual(caught.exception.code, "mind_apply_failed")
        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\noriginal\n")

    @unittest.skipUnless(hasattr(os, "symlink"), "the platform has no symlinks")
    def test_a_symlink_swapped_in_after_resolution_is_refused_by_the_open(self):
        """The read refuses a link itself, not only the check before it.

        `resolve_document` walks with `lstat`, but that check and the open are
        two syscalls. Reaching straight past the walk into the read proves the
        open is independently safe: `O_NOFOLLOW` refuses, so the window between
        them cannot be used to read a file outside the root.
        """
        from cofferdam.workstation.mind.documents import read_document
        from cofferdam.workstation.mind.errors import MindError

        outside = self.home / "outside.md"
        outside.write_text("secrets\n", encoding="utf-8")
        target = self.project_root / "STATUS.md"
        target.unlink()
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this platform cannot create a symlink")

        with self.assertRaises(MindError) as caught:
            read_document(target)
        self.assertEqual(caught.exception.code, "mind_role_unavailable")

    def test_the_apply_uses_no_shell_and_no_subprocess(self):
        import subprocess

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )

        def explode(*args, **kwargs):  # pragma: no cover - the assertion is that this never runs
            raise AssertionError("the mind apply path started a process")

        saved = (subprocess.run, subprocess.Popen, os.system)
        subprocess.run = explode
        subprocess.Popen = explode
        os.system = explode
        try:
            self.mind.accept_proposal(created["proposal_id"])
        finally:
            subprocess.run, subprocess.Popen, os.system = saved

        self.assertEqual(self.project_text("STATUS.md"), "new\n")


class Listing(MindHarness):
    grant_vault = True

    def test_proposals_are_listed_newest_first_and_bounded(self):
        self.activate()
        ids = [
            self.mind.create_proposal(
                scope="project", role="status", content="v" + str(n) + "\n", reason="y"
            )["proposal_id"]
            for n in range(4)
        ]
        listed = self.mind.list_proposals()
        self.assertEqual([p["proposal_id"] for p in listed["proposals"]], list(reversed(ids)))
        self.assertLessEqual(len(listed["proposals"]), listed["limit"])

    def test_a_listing_can_be_filtered_by_state(self):
        self.activate()
        keep = self.mind.create_proposal(
            scope="project", role="status", content="a\n", reason="y"
        )["proposal_id"]
        drop = self.mind.create_proposal(
            scope="project", role="plan", content="b\n", reason="y"
        )["proposal_id"]
        self.mind.reject_proposal(drop)

        pending = self.mind.list_proposals(state="pending")["proposals"]
        self.assertEqual([p["proposal_id"] for p in pending], [keep])

    def test_a_listing_carries_no_content(self):
        self.activate()
        self.mind.create_proposal(
            scope="project", role="status", content="secret sentence\n", reason="y"
        )
        listed = self.mind.list_proposals()
        self.assertNotIn("content", listed["proposals"][0])
        import json

        self.assertNotIn("secret sentence", json.dumps(listed))

    def test_a_single_proposal_carries_its_content_and_live_staleness(self):
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="proposed\n", reason="y"
        )
        fetched = self.mind.get_proposal(created["proposal_id"])
        self.assertEqual(fetched["content"], "proposed\n")
        self.assertFalse(fetched["stale"])

        (self.project_root / "STATUS.md").write_text("drift\n", encoding="utf-8")
        self.assertTrue(self.mind.get_proposal(created["proposal_id"])["stale"])
        # Reading did not change the record: staleness is derived, not stored.
        self.assertEqual(self.mind.get_proposal(created["proposal_id"])["state"], "pending")


class StorePosture(MindHarness):
    grant_vault = True

    def test_the_state_directory_and_database_are_owner_only(self):
        self.activate()
        self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        directory = self.config.state_dir / "mind"
        self.assertTrue(directory.is_dir())
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        for suffix in ("", "-wal", "-shm"):
            path = directory / ("mind.sqlite3" + suffix)
            if path.exists():
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, path.name)

    def test_a_newer_schema_is_refused_rather_than_downgraded(self):
        import sqlite3

        from cofferdam.workstation.mind.store import MindStore
        from cofferdam.workstation.tasks.errors import StoreUnavailable

        self.activate()
        self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        self.mind_store.close()

        path = self.config.state_dir / "mind" / "mind.sqlite3"
        connection = sqlite3.connect(str(path))
        connection.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
        connection.commit()
        connection.close()

        store = MindStore(self.config)
        self.addCleanup(store.close)
        with self.assertRaises(StoreUnavailable):
            store.list_proposals()

    def test_proposal_state_survives_a_restart(self):
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="survives\n", reason="y"
        )
        self.mind_store.close()
        self.build_services()
        self.activate()

        reopened = self.mind.get_proposal(created["proposal_id"])
        self.assertEqual(reopened["state"], "pending")
        self.assertEqual(reopened["content"], "survives\n")
        self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(self.project_text("STATUS.md"), "survives\n")

    def test_deleting_the_workflow_state_leaves_the_markdown_alone(self):
        import shutil

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(self.project_text("STATUS.md"), "new\n")

        self.mind_store.close()
        shutil.rmtree(self.config.state_dir / "mind")
        self.assertEqual(self.project_text("STATUS.md"), "new\n")
        self.assertTrue((self.project_root / "ROADMAP.md").exists())
        self.assertTrue((self.vault_root / "USER.md").exists())

    def test_the_task_database_is_untouched(self):
        self.activate()
        self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        self.assertFalse((self.config.state_dir / "tasks" / "tasks.sqlite3").exists())

    def test_a_proposal_row_stores_no_path_and_no_session(self):
        import sqlite3

        self.activate()
        self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        self.mind_store.close()
        path = self.config.state_dir / "mind" / "mind.sqlite3"
        connection = sqlite3.connect(str(path))
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(memory_proposals)")
            }
            blob = " ".join(
                str(value)
                for row in connection.execute("SELECT * FROM memory_proposals")
                for value in row
            )
        finally:
            connection.close()

        for forbidden in ("path", "root", "session", "token", "provider", "model", "argv"):
            self.assertFalse(
                [c for c in columns if forbidden in c],
                "column matching " + forbidden + " in " + repr(columns),
            )
        self.assertNotIn(str(self.project_root), blob)
        self.assertNotIn("STATUS.md", blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
