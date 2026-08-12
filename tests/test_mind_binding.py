"""A proposal is bound to the bytes **and** to the authority that resolved them.

The base content hash answers "is this still the text I reviewed". It cannot
answer "is this still the same *document*", and the two come apart in a way that
is easy to miss: remap a role from one approved file to another holding
byte-identical content, and a content-only check sees no drift at all and lets a
proposal reviewed against the first land on the second.

Every case here builds exactly that situation — identical bytes, different
authority — so a content check alone would pass and only the binding catches it.
"""

from __future__ import annotations

import json
import unittest

from ._mind_doubles import MindHarness, PROJECT_ID, WORKSPACE_ID

IDENTICAL = "# Status\n\noriginal\n"


class BindingFingerprint(MindHarness):
    """The digest itself: what it separates and what it must not alias."""

    grant_vault = True

    def binding(self, **overrides):
        from pathlib import Path

        from cofferdam.workstation.mind.service import _Target

        fields = {
            "scope": "project",
            "workspace_id": "w",
            "project_id": "p",
            "role": "status",
            "root": Path("/srv/one"),
            "relative": "STATUS.md",
        }
        fields.update(overrides)
        return _Target(**fields).binding_hash

    def test_it_is_deterministic(self):
        self.assertEqual(self.binding(), self.binding())

    def test_every_field_changes_it(self):
        from pathlib import Path

        base = self.binding()
        for field, value in (
            ("scope", "global"),
            ("workspace_id", "other"),
            ("project_id", "other"),
            ("role", "plan"),
            ("root", Path("/srv/two")),
            ("relative", "OTHER.md"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(self.binding(**{field: value}), base)

    def test_field_boundaries_cannot_be_shifted(self):
        """Length prefixing: no arrangement of one input can imitate another.

        With plain concatenation, a workspace `ab` with role `c` and a workspace
        `a` with role `bc` produce the same bytes — two genuinely different
        authorities comparing equal.
        """
        self.assertNotEqual(
            self.binding(workspace_id="ab", project_id="c"),
            self.binding(workspace_id="a", project_id="bc"),
        )

    def test_a_global_target_cannot_alias_a_project_one(self):
        from pathlib import Path

        self.assertNotEqual(
            self.binding(scope="global", workspace_id=None, project_id=None),
            self.binding(scope="global", workspace_id="", project_id="x"),
        )

    def test_it_is_not_the_content_hash(self):
        """Separate domain tags, so the two can never be compared by mistake."""
        from cofferdam.workstation.mind.hashing import (
            TAG_DOCUMENT,
            TAG_TARGET_BINDING,
            document_hash,
            target_binding_hash,
        )

        self.assertNotEqual(TAG_DOCUMENT, TAG_TARGET_BINDING)
        self.assertNotEqual(document_hash(b"x"), target_binding_hash([b"x"]))


class ProjectRoleRemapped(MindHarness):
    """Role remapped to a **byte-identical** different file."""

    grant_vault = False

    def setUp(self) -> None:
        super().setUp()
        # Two files, same bytes. A content check cannot tell them apart.
        (self.project_root / "STATUS.md").write_text(IDENTICAL, encoding="utf-8")
        (self.project_root / "TWIN.md").write_text(IDENTICAL, encoding="utf-8")
        self.write_workspaces(documents={"status": "STATUS.md"})
        self.build_services()
        self.activate()

    def remap_to_twin(self):
        self.write_workspaces(documents={"status": "TWIN.md"})
        self.build_services()
        self.activate()

    def test_the_content_check_alone_would_have_passed(self):
        """The premise. Without this, the test below proves nothing."""
        from cofferdam.workstation.mind.hashing import document_hash

        self.assertEqual(
            document_hash((self.project_root / "STATUS.md").read_bytes()),
            document_hash((self.project_root / "TWIN.md").read_bytes()),
        )

    def test_acceptance_after_a_remap_fails_closed(self):
        from cofferdam.workstation.mind.errors import MindError

        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.remap_to_twin()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_target_authority_changed")

    def test_neither_file_is_touched(self):
        from cofferdam.workstation.mind.errors import MindError

        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.remap_to_twin()
        before = self.snapshot()
        with self.assertRaises(MindError):
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.project_text("STATUS.md"), IDENTICAL)
        self.assertEqual(self.project_text("TWIN.md"), IDENTICAL)

    def test_the_old_target_still_existing_does_not_matter(self):
        """It is not about the old file being gone. It is about *which* file."""
        from cofferdam.workstation.mind.errors import MindError

        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.remap_to_twin()
        self.assertTrue((self.project_root / "STATUS.md").exists())
        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_target_authority_changed")

    def test_the_reason_is_recorded_durably(self):
        from cofferdam.workstation.mind.errors import MindError

        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.remap_to_twin()
        with self.assertRaises(MindError):
            self.mind.accept_proposal(created["proposal_id"])
        stored = self.mind.get_proposal(created["proposal_id"])
        self.assertEqual(stored["state"], "stale")
        self.assertEqual(stored["decided_reason"], "authority_changed")

    def test_the_read_side_flag_says_so_before_anybody_presses_accept(self):
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.assertFalse(self.mind.get_proposal(created["proposal_id"])["stale"])
        self.remap_to_twin()
        self.assertTrue(self.mind.get_proposal(created["proposal_id"])["stale"])

    def test_an_unchanged_binding_with_unchanged_content_still_applies(self):
        """The control. A binding check that refused everything would also pass
        every test above."""
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        # Rewrite the same configuration, byte for byte, and rebuild everything.
        self.write_workspaces(documents={"status": "STATUS.md"})
        self.build_services()
        self.activate()

        result = self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(result["state"], "applied")
        self.assertEqual(self.project_text("STATUS.md"), "rewritten\n")
        self.assertEqual(self.project_text("TWIN.md"), IDENTICAL)


class ProjectAuthorityChanged(MindHarness):
    """The project root moves under an unchanged workspace and role."""

    grant_vault = False

    def setUp(self) -> None:
        super().setUp()
        self.twin_root = self.home / "projects" / "twin"
        self.twin_root.mkdir(parents=True)
        (self.twin_root / "STATUS.md").write_text(IDENTICAL, encoding="utf-8")
        (self.project_root / "STATUS.md").write_text(IDENTICAL, encoding="utf-8")
        self.write_workspaces(documents={"status": "STATUS.md"})
        self.build_services()
        self.activate()

    def point_project_at_the_twin(self):
        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "root": str(self.twin_root),
                            "adapters": ["validation"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.build_services()
        self.activate()

    def test_a_re_pointed_project_root_fails_closed(self):
        from cofferdam.workstation.mind.errors import MindError

        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.point_project_at_the_twin()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_target_authority_changed")
        self.assertEqual(self.project_text("STATUS.md"), IDENTICAL)
        self.assertEqual((self.twin_root / "STATUS.md").read_text(encoding="utf-8"), IDENTICAL)

    def test_a_workspace_rebound_to_another_project_fails_closed(self):
        from cofferdam.workstation.mind.errors import MindError

        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {"project_id": PROJECT_ID, "root": str(self.project_root),
                         "adapters": ["validation"]},
                        {"project_id": "twin", "root": str(self.twin_root),
                         "adapters": ["validation"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.build_services()
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )

        # Same workspace id, same role, same file name — a different project.
        (self.config.config_dir / "workspaces.json").write_text(
            json.dumps(
                {
                    "workspaces": [
                        {
                            "workspace_id": WORKSPACE_ID,
                            "project_id": "twin",
                            "documents": {"status": "STATUS.md"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.build_services()
        self.activate()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_target_authority_changed")
        self.assertEqual((self.twin_root / "STATUS.md").read_text(encoding="utf-8"), IDENTICAL)


class GlobalAuthorityChanged(MindHarness):
    """The granted vault root moves to a byte-identical target."""

    grant_vault = True

    def setUp(self) -> None:
        super().setUp()
        self.twin_vault = self.home / "twin-vault"
        self.twin_vault.mkdir()
        (self.twin_vault / "USER.md").write_text(IDENTICAL, encoding="utf-8")
        (self.vault_root / "USER.md").write_text(IDENTICAL, encoding="utf-8")

    def test_a_re_granted_vault_root_fails_closed(self):
        from cofferdam.workstation.mind.errors import MindError

        created = self.mind.create_proposal(
            scope="global", role="user", content="rewritten\n", reason="y"
        )
        self.write_grant(root=self.twin_vault, documents={"user": "USER.md"})
        self.build_services()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_target_authority_changed")
        self.assertEqual(self.vault_text("USER.md"), IDENTICAL)
        self.assertEqual((self.twin_vault / "USER.md").read_text(encoding="utf-8"), IDENTICAL)

    def test_a_re_mapped_global_role_fails_closed(self):
        from cofferdam.workstation.mind.errors import MindError

        (self.vault_root / "TWIN.md").write_text(IDENTICAL, encoding="utf-8")
        created = self.mind.create_proposal(
            scope="global", role="user", content="rewritten\n", reason="y"
        )
        self.write_grant(documents={"user": "TWIN.md"})
        self.build_services()

        with self.assertRaises(MindError) as caught:
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(caught.exception.code, "mind_target_authority_changed")
        self.assertEqual(self.vault_text("USER.md"), IDENTICAL)
        self.assertEqual(self.vault_text("TWIN.md"), IDENTICAL)

    def test_an_unchanged_grant_still_applies(self):
        created = self.mind.create_proposal(
            scope="global", role="user", content="rewritten\n", reason="y"
        )
        self.write_grant()
        self.build_services()
        self.assertEqual(
            self.mind.accept_proposal(created["proposal_id"])["state"], "applied"
        )
        self.assertEqual(self.vault_text("USER.md"), "rewritten\n")


class BindingIsNotPublished(MindHarness):
    """An opaque fingerprint is durable; it is not something a client is told."""

    grant_vault = True

    def test_no_payload_carries_the_binding_hash(self):
        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="new\n", reason="y"
        )
        stored_hash = None
        import sqlite3

        for payload in (
            created,
            self.mind.get_proposal(created["proposal_id"]),
            self.mind.list_proposals(),
            self.mind.available(),
        ):
            self.assertNotIn("target_binding_hash", json.dumps(payload))

        self.mind_store.close()
        connection = sqlite3.connect(str(self.config.state_dir / "mind" / "mind.sqlite3"))
        try:
            stored_hash = connection.execute(
                "SELECT target_binding_hash FROM memory_proposals"
            ).fetchone()[0]
        finally:
            connection.close()

        # It is stored, so the check is real...
        self.assertEqual(len(stored_hash), 64)
        # ...and it is not a path, nor reversible into one.
        self.assertNotIn(str(self.project_root), stored_hash)
        self.assertNotIn("STATUS.md", stored_hash)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
