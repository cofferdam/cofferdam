"""One real Mind service over real temporary directories.

Deliberately not a mock. Every property this milestone claims — containment, the
symlink walk, the base hash, the atomic replace, the permissions on the state
directory — is a property of the *filesystem*, and a double that returns strings
would assert nothing about any of them.

Nothing here writes to the user's home, reads an existing vault, or reaches the
network: the whole world is a `TemporaryDirectory` per test.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ID = "demo"
WORKSPACE_ID = "demo-workspace"


class MindHarness(unittest.TestCase):
    """A workstation home, one project, one workspace, and optionally a vault."""

    #: Whether `setUp` writes a `mind-grant.json`. The default is off, because
    #: the default state of the product is that no vault has been granted.
    grant_vault = False

    project_documents = {"status": "STATUS.md", "plan": "ROADMAP.md"}
    vault_documents = {
        "user": "USER.md",
        "preferences": "PREFERENCES.md",
        "cross_project": "CROSS_PROJECT.md",
    }

    def setUp(self) -> None:
        from cofferdam.workstation.config import load_config

        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)

        self.project_root = self.home / "projects" / PROJECT_ID
        self.project_root.mkdir(parents=True)
        (self.project_root / "STATUS.md").write_text("# Status\n\noriginal\n", encoding="utf-8")
        (self.project_root / "ROADMAP.md").write_text("# Roadmap\n\noriginal\n", encoding="utf-8")
        (self.project_root / "UNRELATED.md").write_text("do not touch\n", encoding="utf-8")

        self.vault_root = self.home / "vault"
        self.vault_root.mkdir()
        (self.vault_root / "USER.md").write_text("# User\n\noriginal\n", encoding="utf-8")
        (self.vault_root / "PREFERENCES.md").write_text("# Preferences\n", encoding="utf-8")
        (self.vault_root / "CROSS_PROJECT.md").write_text(
            "# Cross-project\n\noriginal\n", encoding="utf-8"
        )

        self.config = load_config(self.home)
        self.config.ensure_dirs()
        self.write_projects()
        self.write_workspaces()
        if self.grant_vault:
            self.write_grant()

        self.build_services()

    # -- configuration -------------------------------------------------------

    def write_projects(self) -> None:
        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "project_id": PROJECT_ID,
                            "display_name": "Demo project",
                            "root": str(self.project_root),
                            "adapters": ["validation"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def write_workspaces(self, documents=None) -> None:
        entry = {
            "workspace_id": WORKSPACE_ID,
            "display_name": "Demo workspace",
            "project_id": PROJECT_ID,
        }
        mapping = self.project_documents if documents is None else documents
        if mapping is not None:
            entry["documents"] = dict(mapping)
        (self.config.config_dir / "workspaces.json").write_text(
            json.dumps({"workspaces": [entry]}), encoding="utf-8"
        )

    def write_grant(self, *, root=None, documents=None, enabled=True, omit_enabled=False) -> None:
        """Write the grant. ``enabled: true`` is what activates it.

        ``omit_enabled`` reaches the state D-2026-08-12-2 made fail closed: a
        grant somebody wrote and never turned on. It is a parameter rather than
        a separate helper because the tests need to move a live host *between*
        these states while a proposal is pending.
        """
        vault = {
            "root": str(self.vault_root if root is None else root),
            "documents": dict(self.vault_documents if documents is None else documents),
        }
        if not omit_enabled:
            vault["enabled"] = enabled
        (self.config.config_dir / "mind-grant.json").write_text(
            json.dumps({"global_vault": vault}), encoding="utf-8"
        )

    def remove_grant(self) -> None:
        path = self.config.config_dir / "mind-grant.json"
        if path.exists():
            path.unlink()

    # -- services ------------------------------------------------------------

    def build_services(self) -> None:
        from cofferdam.workstation.mind.service import MindService
        from cofferdam.workstation.mind.store import MindStore
        from cofferdam.workstation.tasks.projects import load_projects
        from cofferdam.workstation.workspace.service import WorkspaceService
        from cofferdam.workstation.workspace.store import WorkspaceStore

        self.workspace_store = WorkspaceStore(self.config)
        self.addCleanup(self.workspace_store.close)
        self.workspaces = WorkspaceService(
            self.config,
            self.workspace_store,
            projects=lambda: load_projects(self.config, ("validation",)),
        )
        self.mind_store = MindStore(self.config)
        self.addCleanup(self.mind_store.close)
        self.mind = MindService(self.config, self.mind_store, workspaces=self.workspaces)

    def activate(self) -> None:
        self.workspaces.reload_workspaces()
        self.workspaces.activate(WORKSPACE_ID)

    # -- reading the disk directly -------------------------------------------

    def project_text(self, name: str) -> str:
        return (self.project_root / name).read_text(encoding="utf-8")

    def vault_text(self, name: str) -> str:
        return (self.vault_root / name).read_text(encoding="utf-8")

    def snapshot(self) -> dict:
        """Every file under the project root and the vault, by content."""
        seen = {}
        for root in (self.project_root, self.vault_root):
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    seen[str(path)] = path.read_bytes()
        return seen
