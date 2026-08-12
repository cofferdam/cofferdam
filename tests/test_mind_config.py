"""Host-owned Mind configuration: document roles and the global vault grant.

The question every case here asks is *who decided this*. A role mapping and a
vault grant are the two pieces of configuration that turn a semantic word into a
real file on somebody's disk, so the failures worth testing are the ones where
something other than the host quietly gets to decide:

* a role that resolves to a file the operator did not name;
* a grant that exists because a directory happened to be there;
* two mappings for one role, where load order picks the winner;
* a configuration field that smuggles a path, a command or a credential in.

None of this touches the API. These are the loaders.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class MindConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from cofferdam.workstation.config import load_config

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.config = load_config(self.home)
        self.config.ensure_dirs()

    def write_workspaces(self, document: object) -> None:
        (self.config.config_dir / "workspaces.json").write_text(
            document if isinstance(document, str) else json.dumps(document),
            encoding="utf-8",
        )

    def write_grant(self, document: object) -> None:
        (self.config.config_dir / "mind-grant.json").write_text(
            document if isinstance(document, str) else json.dumps(document),
            encoding="utf-8",
        )


class Roles(MindConfigTestCase):
    """The code owns the role vocabulary; configuration only fills it in."""

    def test_the_project_role_vocabulary_is_closed(self):
        from cofferdam.workstation.mind.roles import PROJECT_ROLES, valid_role, SCOPE_PROJECT

        self.assertIn("status", PROJECT_ROLES)
        self.assertIn("decisions", PROJECT_ROLES)
        self.assertTrue(valid_role(SCOPE_PROJECT, "plan"))
        self.assertFalse(valid_role(SCOPE_PROJECT, "anything-else"))
        self.assertFalse(valid_role(SCOPE_PROJECT, "user"))

    def test_the_global_role_vocabulary_is_closed_and_separate(self):
        from cofferdam.workstation.mind.roles import GLOBAL_ROLES, valid_role, SCOPE_GLOBAL

        self.assertEqual(set(GLOBAL_ROLES), {"user", "communication_style", "preferences"})
        self.assertTrue(valid_role(SCOPE_GLOBAL, "user"))
        # A project role does not become a global one by being asked for.
        self.assertFalse(valid_role(SCOPE_GLOBAL, "status"))

    def test_an_unknown_scope_has_no_roles(self):
        from cofferdam.workstation.mind.roles import roles_for_scope, valid_role

        self.assertEqual(roles_for_scope("planner"), ())
        self.assertFalse(valid_role("planner", "status"))

    def test_a_document_name_is_relative_and_contained(self):
        from cofferdam.workstation.mind.roles import valid_document_name

        self.assertEqual(valid_document_name("STATUS.md"), "STATUS.md")
        self.assertEqual(valid_document_name("memory/PLAN.md"), "memory/PLAN.md")
        for hostile in (
            "../STATUS.md",
            "memory/../../STATUS.md",
            "/etc/passwd",
            "~/STATUS.md",
            "$HOME/STATUS.md",
            "./STATUS.md",
            "memory//PLAN.md",
            "",
            "   ",
            "STATUS.md\x00",
            "C:\\STATUS.md",
            "a/" * 40 + "STATUS.md",
            None,
            42,
        ):
            with self.subTest(name=hostile):
                self.assertIsNone(valid_document_name(hostile))


class WorkspaceDocumentRoles(MindConfigTestCase):
    """`documents` on a workspace: the mapping PR1 deliberately left out."""

    def test_a_workspace_carries_a_role_map(self):
        from cofferdam.workstation.workspace.models import load_workspaces

        self.write_workspaces(
            {
                "workspaces": [
                    {
                        "workspace_id": "cofferdam",
                        "project_id": "cofferdam",
                        "documents": {"status": "STATUS.md", "plan": "ROADMAP.md"},
                    }
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(registry.problems, ())
        workspace = registry.get("cofferdam")
        self.assertEqual(workspace.documents["status"], "STATUS.md")
        self.assertEqual(workspace.documents["plan"], "ROADMAP.md")

    def test_a_workspace_without_documents_is_still_valid(self):
        from cofferdam.workstation.workspace.models import load_workspaces

        self.write_workspaces(
            {"workspaces": [{"workspace_id": "demo", "project_id": "demo"}]}
        )
        registry = load_workspaces(self.config)
        self.assertEqual(registry.problems, ())
        self.assertEqual(registry.get("demo").documents, {})

    def test_a_duplicate_role_key_fails_the_entry_closed(self):
        """`json.loads` keeps the last duplicate key. That is load order deciding.

        Two mappings for one role is exactly the ambiguity that must never be
        resolved by position, so the parser refuses duplicate keys outright.
        """
        from cofferdam.workstation.workspace.models import load_workspaces

        self.write_workspaces(
            '{"workspaces": [{"workspace_id": "demo", "project_id": "demo",'
            ' "documents": {"status": "STATUS.md", "status": "OTHER.md"}}]}'
        )
        registry = load_workspaces(self.config)
        self.assertEqual(registry.workspaces, ())
        self.assertTrue(registry.problems)

    def test_an_unknown_role_name_is_refused(self):
        from cofferdam.workstation.workspace.models import load_workspaces

        self.write_workspaces(
            {
                "workspaces": [
                    {
                        "workspace_id": "demo",
                        "project_id": "demo",
                        "documents": {"secrets": "SECRETS.md"},
                    }
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(registry.workspaces, ())
        self.assertTrue(any("role" in p["problem"] for p in registry.problems))

    def test_a_traversing_document_value_is_refused(self):
        from cofferdam.workstation.workspace.models import load_workspaces

        for hostile in ("../../../etc/passwd", "/etc/passwd", "~/notes.md"):
            with self.subTest(value=hostile):
                self.write_workspaces(
                    {
                        "workspaces": [
                            {
                                "workspace_id": "demo",
                                "project_id": "demo",
                                "documents": {"status": hostile},
                            }
                        ]
                    }
                )
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())
                self.assertTrue(registry.problems)

    def test_a_global_role_cannot_be_mapped_on_a_workspace(self):
        """The vault is not reachable by writing a workspace entry."""
        from cofferdam.workstation.workspace.models import load_workspaces

        self.write_workspaces(
            {
                "workspaces": [
                    {
                        "workspace_id": "demo",
                        "project_id": "demo",
                        "documents": {"user": "USER.md"},
                    }
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(registry.workspaces, ())

    def test_the_forbidden_field_list_still_refuses_a_root(self):
        """`documents` widens the schema; it does not reopen path authority."""
        from cofferdam.workstation.workspace.models import load_workspaces

        self.write_workspaces(
            {
                "workspaces": [
                    {"workspace_id": "demo", "project_id": "demo", "root": "/tmp"}
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(registry.workspaces, ())


class GlobalVaultGrant(MindConfigTestCase):
    """The second filesystem grant, and the fact that it is absent by default."""

    def test_no_file_means_no_vault(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        grant = load_mind_grant(self.config)
        self.assertIsNone(grant.vault)
        self.assertFalse(grant.source_present)
        self.assertEqual(grant.problems, ())

    def test_nothing_is_discovered_by_scanning(self):
        """A vault-shaped directory next door is not a grant."""
        from cofferdam.workstation.mind.grant import load_mind_grant

        looks_like_a_vault = self.home / "Documents" / "Obsidian"
        looks_like_a_vault.mkdir(parents=True)
        (looks_like_a_vault / "USER.md").write_text("# me\n", encoding="utf-8")
        (looks_like_a_vault / ".obsidian").mkdir()

        self.assertIsNone(load_mind_grant(self.config).vault)

    def test_a_grant_names_an_absolute_literal_root(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        vault = self.home / "vault"
        vault.mkdir()
        self.write_grant(
            {"global_vault": {"root": str(vault), "documents": {"user": "USER.md"}}}
        )
        grant = load_mind_grant(self.config)
        self.assertIsNotNone(grant.vault)
        self.assertEqual(grant.vault.root, vault)
        self.assertTrue(grant.vault.enabled)

    def test_a_relative_or_expandable_root_is_refused(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        for hostile in ("vault", "~/vault", "$HOME/vault", "/tmp/../tmp/vault", ""):
            with self.subTest(root=hostile):
                self.write_grant({"global_vault": {"root": hostile}})
                grant = load_mind_grant(self.config)
                self.assertIsNone(grant.vault)
                self.assertTrue(grant.problems)

    def test_a_disabled_grant_yields_no_vault(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        vault = self.home / "vault"
        vault.mkdir()
        self.write_grant({"global_vault": {"root": str(vault), "enabled": False}})
        self.assertIsNone(load_mind_grant(self.config).vault)

    def test_a_non_boolean_enabled_is_refused_rather_than_coerced(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        vault = self.home / "vault"
        vault.mkdir()
        self.write_grant({"global_vault": {"root": str(vault), "enabled": "yes"}})
        grant = load_mind_grant(self.config)
        self.assertIsNone(grant.vault)
        self.assertTrue(grant.problems)

    def test_forbidden_fields_are_refused_by_name(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        vault = self.home / "vault"
        vault.mkdir()
        for field in ("command", "exec", "token", "adapter", "prompt"):
            with self.subTest(field=field):
                self.write_grant({"global_vault": {"root": str(vault), field: "x"}})
                grant = load_mind_grant(self.config)
                self.assertIsNone(grant.vault)
                self.assertTrue(grant.problems)

    def test_a_project_role_cannot_be_mapped_in_the_vault(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        vault = self.home / "vault"
        vault.mkdir()
        self.write_grant(
            {"global_vault": {"root": str(vault), "documents": {"status": "STATUS.md"}}}
        )
        grant = load_mind_grant(self.config)
        self.assertIsNone(grant.vault)

    def test_a_duplicate_role_key_in_the_grant_fails_closed(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        vault = self.home / "vault"
        vault.mkdir()
        self.write_grant(
            '{"global_vault": {"root": ' + json.dumps(str(vault)) + ','
            ' "documents": {"user": "USER.md", "user": "OTHER.md"}}}'
        )
        grant = load_mind_grant(self.config)
        self.assertIsNone(grant.vault)
        self.assertTrue(grant.problems)

    def test_a_malformed_document_never_becomes_a_partial_grant(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        self.write_grant("{ not json")
        grant = load_mind_grant(self.config)
        self.assertIsNone(grant.vault)
        self.assertTrue(grant.problems)

    def test_a_problem_never_carries_the_configured_path(self):
        from cofferdam.workstation.mind.grant import load_mind_grant

        self.write_grant({"global_vault": {"root": "/very/private/place", "enabled": "yes"}})
        grant = load_mind_grant(self.config)
        for problem in grant.problems:
            self.assertNotIn("/very/private/place", json.dumps(problem))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
