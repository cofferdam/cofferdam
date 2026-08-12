"""Reading the mind: by role, under a grant, inside a root, or not at all.

This is the file that says what a caller cannot do. Cofferdam's whole memory
boundary rests on one claim — **a caller names a semantic role and never a
file** — and every case below is an attempt to get a byte out of the host by
some other route: a path in a request, a link out of a root, a role nobody
mapped, a vault nobody granted.

Fail-closed is the expected answer to all of them.
"""

from __future__ import annotations

import os
import unittest

from ._mind_doubles import MindHarness, WORKSPACE_ID


class NoGrant(MindHarness):
    """The default state of the product: there is no global mind."""

    grant_vault = False

    def test_the_global_mind_is_not_readable(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("global", "user")
        self.assertEqual(caught.exception.code, "mind_global_grant_missing")

    def test_no_global_role_is_even_listed(self):
        self.activate()
        payload = self.mind.available()
        self.assertFalse(payload["global_vault"]["granted"])
        self.assertEqual(
            [d for d in payload["documents"] if d["scope"] == "global"], []
        )

    def test_a_proposal_against_the_global_mind_is_refused(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.create_proposal(scope="global", role="user", content="x", reason="y")
        self.assertEqual(caught.exception.code, "mind_global_grant_missing")

    def test_a_vault_that_exists_on_disk_is_still_not_granted(self):
        """The directory is right there. It is still not the global mind."""
        self.assertTrue((self.vault_root / "USER.md").exists())
        self.activate()
        self.assertFalse(self.mind.available()["global_vault"]["granted"])


class GrantedVault(MindHarness):
    grant_vault = True

    def test_an_approved_global_role_reads(self):
        self.activate()
        document = self.mind.read_document("global", "user")
        self.assertEqual(document["scope"], "global")
        self.assertEqual(document["role"], "user")
        self.assertIn("original", document["content"])

    def test_an_unmapped_global_role_is_not_a_filename_guess(self):
        from cofferdam.workstation.mind.errors import MindError

        self.write_grant(documents={"user": "USER.md"})
        self.build_services()
        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("global", "preferences")
        self.assertEqual(caught.exception.code, "mind_role_unconfigured")
        # And the file it would have guessed does exist, which is the point.
        self.assertTrue((self.vault_root / "PREFERENCES.md").exists())

    def test_a_mapped_but_missing_file_refuses(self):
        from cofferdam.workstation.mind.errors import MindError

        (self.vault_root / "USER.md").unlink()
        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("global", "user")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")

    def test_reading_the_vault_does_not_need_an_active_workspace(self):
        """Global memory is not scoped to a project."""
        document = self.mind.read_document("global", "user")
        self.assertIn("original", document["content"])

    @unittest.skipUnless(hasattr(os, "symlink"), "the platform has no symlinks")
    def test_a_symlinked_document_inside_the_vault_is_refused(self):
        outside = self.home / "outside.md"
        outside.write_text("secrets\n", encoding="utf-8")
        target = self.vault_root / "USER.md"
        target.unlink()
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this platform cannot create a symlink")

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document("global", "user")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")

    @unittest.skipUnless(hasattr(os, "symlink"), "the platform has no symlinks")
    def test_a_symlinked_vault_root_is_refused(self):
        real = self.home / "real-vault"
        real.mkdir()
        (real / "USER.md").write_text("elsewhere\n", encoding="utf-8")
        link = self.home / "linked-vault"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this platform cannot create a symlink")
        self.write_grant(root=link)
        self.build_services()

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document("global", "user")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")


class ProjectRoles(MindHarness):
    grant_vault = False

    def test_a_role_resolves_through_the_active_workspace(self):
        self.activate()
        document = self.mind.read_document("project", "status")
        self.assertEqual(document["role"], "status")
        self.assertIn("original", document["content"])

    def test_with_no_active_workspace_there_is_no_project_mind(self):
        from cofferdam.workstation.workspace.errors import WorkspaceError

        with self.assertRaises(WorkspaceError) as caught:
            self.mind.read_document("project", "status")
        self.assertEqual(caught.exception.code, "workspace_active_unset")

    def test_an_unmapped_role_is_refused_rather_than_guessed(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("project", "decisions")
        self.assertEqual(caught.exception.code, "mind_role_unconfigured")

    def test_an_unknown_role_word_is_refused(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("project", "passwd")
        self.assertEqual(caught.exception.code, "mind_role_invalid")

    def test_an_unknown_scope_is_refused(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("filesystem", "status")
        self.assertEqual(caught.exception.code, "mind_scope_invalid")

    def test_a_disabled_project_makes_the_project_mind_unreachable(self):
        from cofferdam.workstation.workspace.errors import WorkspaceError

        self.activate()
        import json

        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": "demo",
                            "root": str(self.project_root),
                            "enabled": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(WorkspaceError) as caught:
            self.mind.read_document("project", "status")
        self.assertEqual(caught.exception.code, "workspace_project_missing")

    @unittest.skipUnless(hasattr(os, "symlink"), "the platform has no symlinks")
    def test_a_document_symlinked_out_of_the_project_is_refused(self):
        outside = self.home / "outside.md"
        outside.write_text("secrets\n", encoding="utf-8")
        target = self.project_root / "STATUS.md"
        target.unlink()
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this platform cannot create a symlink")
        self.activate()

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document("project", "status")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")

    @unittest.skipUnless(hasattr(os, "symlink"), "the platform has no symlinks")
    def test_a_symlinked_intermediate_directory_is_refused(self):
        outside = self.home / "elsewhere"
        outside.mkdir()
        (outside / "PLAN.md").write_text("secrets\n", encoding="utf-8")
        try:
            os.symlink(outside, self.project_root / "memory")
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this platform cannot create a symlink")
        self.write_workspaces(documents={"plan": "memory/PLAN.md"})
        self.build_services()
        self.activate()

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document("project", "plan")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")

    def test_a_directory_is_not_a_document(self):
        from cofferdam.workstation.mind.errors import MindError

        (self.project_root / "STATUS.md").unlink()
        (self.project_root / "STATUS.md").mkdir()
        self.activate()
        with self.assertRaises(MindError) as caught:
            self.mind.read_document("project", "status")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")


class NoPathAuthority(MindHarness):
    """There is no argument anywhere that carries a path."""

    grant_vault = True

    def test_the_service_signature_has_no_path_vocabulary(self):
        import inspect

        from cofferdam.workstation.mind.service import MindService

        forbidden = {
            "absolute_path",
            "cwd",
            "directory",
            "file",
            "filename",
            "path",
            "relative_path",
            "root",
            "uri",
            "url",
            "vault",
        }
        for name in (
            "read_document",
            "create_proposal",
            "accept_proposal",
            "reject_proposal",
            "get_proposal",
            "list_proposals",
            "available",
        ):
            with self.subTest(method=name):
                parameters = set(inspect.signature(getattr(MindService, name)).parameters)
                self.assertEqual(parameters & forbidden, set())

    def test_a_role_that_is_a_path_never_reaches_the_filesystem(self):
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        for hostile in (
            "../../../etc/passwd",
            "/etc/passwd",
            "STATUS.md",
            "~/.ssh/id_rsa",
            "status/../../../etc/passwd",
        ):
            with self.subTest(role=hostile):
                with self.assertRaises(MindError) as caught:
                    self.mind.read_document("project", hostile)
                self.assertEqual(caught.exception.code, "mind_role_invalid")

    def test_no_published_payload_carries_a_path(self):
        import json

        self.activate()
        blobs = [
            json.dumps(self.mind.available()),
            json.dumps(self.mind.read_document("project", "status")),
            json.dumps(self.mind.read_document("global", "user")),
            json.dumps(self.mind.list_proposals()),
        ]
        for blob in blobs:
            self.assertNotIn(str(self.project_root), blob)
            self.assertNotIn(str(self.vault_root), blob)
            self.assertNotIn(str(self.home), blob)
            self.assertNotIn("STATUS.md", blob)
            self.assertNotIn("USER.md", blob)

    def test_available_reports_roles_and_availability_only(self):
        self.activate()
        payload = self.mind.available()
        for entry in payload["documents"]:
            self.assertEqual(
                set(entry),
                {"scope", "role", "available", "bytes", "content_hash", "modified_at"},
            )


class ReadsChangeNothing(MindHarness):
    grant_vault = True

    def test_reading_creates_no_state_database(self):
        self.activate()
        self.mind.available()
        self.mind.read_document("project", "status")
        self.mind.list_proposals()
        self.assertFalse((self.config.state_dir / "mind").exists())

    def test_reading_does_not_touch_the_documents(self):
        self.activate()
        before = self.snapshot()
        self.mind.available()
        self.mind.read_document("project", "status")
        self.mind.read_document("global", "user")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
