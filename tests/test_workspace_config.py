"""The workspace configuration loader: what it accepts, and what it refuses by name.

The interesting half of this file is the refusals. A workspace is a small object
and validating five fields is not hard; what is worth pinning is that a workspace
can never acquire the two authorities it sits next to — the project's directory
and the project's choice of adapter — because both would be invisible mistakes.
A `root` on a workspace would work fine until it disagreed with the project. An
`adapters` list would work fine until it disagreed with `delegated_adapter`, which
is the exact bug PR #34 removed one level down.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cofferdam.workstation.workspace.errors import WorkspaceDisabled, WorkspaceUnknown
from cofferdam.workstation.workspace.models import (
    FORBIDDEN_WORKSPACE_FIELDS,
    MAX_WORKSPACES,
    Workspace,
    load_workspaces,
    valid_workspace_id,
)


class _Config:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir


class WorkspaceConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmp.name)
        self.config = _Config(self.config_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, document) -> None:
        (self.config_dir / "workspaces.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    # -- the happy path ------------------------------------------------------

    def test_valid_workspace_loads(self):
        self.write(
            {
                "workspaces": [
                    {
                        "workspace_id": "cofferdam",
                        "display_name": "Cofferdam",
                        "project_id": "cofferdam",
                        "notes": "the main repository",
                    }
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(len(registry.workspaces), 1)
        self.assertEqual(registry.problems, ())
        self.assertTrue(registry.source_present)
        workspace = registry.workspaces[0]
        self.assertEqual(workspace.workspace_id, "cofferdam")
        self.assertEqual(workspace.display_name, "Cofferdam")
        self.assertEqual(workspace.project_id, "cofferdam")
        self.assertTrue(workspace.enabled)

    def test_display_name_defaults_to_the_id(self):
        self.write({"workspaces": [{"workspace_id": "w1", "project_id": "p1"}]})
        registry = load_workspaces(self.config)
        self.assertEqual(registry.workspaces[0].display_name, "w1")

    def test_missing_file_is_not_an_error(self):
        """The shipped default, and the whole backward-compatibility posture."""
        registry = load_workspaces(self.config)
        self.assertEqual(registry.workspaces, ())
        self.assertEqual(registry.problems, ())
        self.assertFalse(registry.source_present)

    # -- refusals ------------------------------------------------------------

    def test_duplicate_workspace_id_keeps_the_first_and_reports(self):
        self.write(
            {
                "workspaces": [
                    {"workspace_id": "dup", "project_id": "p1", "display_name": "First"},
                    {"workspace_id": "dup", "project_id": "p2", "display_name": "Second"},
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(len(registry.workspaces), 1)
        self.assertEqual(registry.workspaces[0].display_name, "First")
        self.assertEqual(registry.workspaces[0].project_id, "p1")
        self.assertEqual(len(registry.problems), 1)
        self.assertIn("duplicate", registry.problems[0]["problem"])

    def test_unknown_field_is_kept_rather_than_refused(self):
        """An unrecognised key is not fatal — but a *forbidden* one is.

        The distinction is deliberate. A key this build does not know may be a
        configuration written for a later version, and dropping the workspace
        would break a host on upgrade-then-rollback. A key on the forbidden list
        is different in kind: it is an attempt to put a decision here that lives
        somewhere else, and that must be loud.
        """
        self.write(
            {
                "workspaces": [
                    {"workspace_id": "w1", "project_id": "p1", "future_field": "hello"}
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(len(registry.workspaces), 1)
        self.assertEqual(registry.problems, ())

    def test_every_forbidden_field_is_refused_by_name(self):
        for field in sorted(FORBIDDEN_WORKSPACE_FIELDS):
            with self.subTest(field=field):
                self.write(
                    {
                        "workspaces": [
                            {"workspace_id": "w1", "project_id": "p1", field: "anything"}
                        ]
                    }
                )
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, (), field + " must not load")
                self.assertEqual(len(registry.problems), 1)
                self.assertIn(field, registry.problems[0]["problem"])

    def test_a_workspace_cannot_select_an_adapter_or_model(self):
        """The refusal that matters most, stated on its own.

        PR #34 made "which agent runs here" an explicit decision on the project
        after finding ordering had silently been the authority. A workspace that
        could name an adapter would recreate that one level up — and worse, since
        the workspace is the thing a client switches.
        """
        for field in ("adapter", "adapters", "delegated_adapter", "model", "provider"):
            with self.subTest(field=field):
                self.write(
                    {
                        "workspaces": [
                            {
                                "workspace_id": "w1",
                                "project_id": "p1",
                                field: "claude-agent-sdk",
                            }
                        ]
                    }
                )
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())
                self.assertIn(field, registry.problems[0]["problem"])

    def test_a_workspace_cannot_introduce_a_path(self):
        for field in ("root", "path", "directory"):
            with self.subTest(field=field):
                self.write(
                    {
                        "workspaces": [
                            {"workspace_id": "w1", "project_id": "p1", field: "/etc"}
                        ]
                    }
                )
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())
                self.assertIn(field, registry.problems[0]["problem"])

    def test_invalid_workspace_id_is_refused(self):
        for bad in ("With Spaces", "UPPER", "../escape", "", 7, None, "x" * 65):
            with self.subTest(value=bad):
                self.write({"workspaces": [{"workspace_id": bad, "project_id": "p1"}]})
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())
                self.assertEqual(len(registry.problems), 1)

    def test_invalid_project_id_is_refused(self):
        for bad in ("With Spaces", "../escape", "", 7, None):
            with self.subTest(value=bad):
                self.write({"workspaces": [{"workspace_id": "w1", "project_id": bad}]})
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())

    def test_enabled_must_be_a_boolean(self):
        for bad in ("true", 1, "yes", None):
            with self.subTest(value=bad):
                self.write(
                    {
                        "workspaces": [
                            {"workspace_id": "w1", "project_id": "p1", "enabled": bad}
                        ]
                    }
                )
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())
                self.assertIn("enabled", registry.problems[0]["problem"])

    def test_malformed_document_fails_closed(self):
        for raw in ("not json", "[]", '{"workspaces": {}}', '{"other": []}'):
            with self.subTest(raw=raw):
                (self.config_dir / "workspaces.json").write_text(raw, encoding="utf-8")
                registry = load_workspaces(self.config)
                self.assertEqual(registry.workspaces, ())
                self.assertTrue(registry.source_present)
                self.assertTrue(registry.problems)

    def test_one_bad_entry_never_takes_the_others_down(self):
        self.write(
            {
                "workspaces": [
                    {"workspace_id": "good", "project_id": "p1"},
                    {"workspace_id": "bad", "project_id": "p2", "root": "/etc"},
                    {"workspace_id": "also-good", "project_id": "p3"},
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertEqual(
            [w.workspace_id for w in registry.workspaces], ["good", "also-good"]
        )
        self.assertEqual(len(registry.problems), 1)

    def test_the_list_is_bounded(self):
        self.write(
            {
                "workspaces": [
                    {"workspace_id": "w%d" % index, "project_id": "p1"}
                    for index in range(MAX_WORKSPACES + 20)
                ]
            }
        )
        registry = load_workspaces(self.config)
        self.assertLessEqual(len(registry.workspaces), MAX_WORKSPACES)

    # -- ordering is never authority ----------------------------------------

    def test_lookup_is_by_id_and_independent_of_file_order(self):
        forward = [
            {"workspace_id": "alpha", "project_id": "p1"},
            {"workspace_id": "beta", "project_id": "p2"},
        ]
        self.write({"workspaces": forward})
        first = load_workspaces(self.config).get("beta")
        self.write({"workspaces": list(reversed(forward))})
        second = load_workspaces(self.config).get("beta")
        self.assertEqual(first, second)
        self.assertEqual(second.project_id, "p2")

    def test_no_workspace_is_ever_a_default(self):
        """An unknown id refuses; it never resolves to whichever is first."""
        self.write(
            {
                "workspaces": [
                    {"workspace_id": "alpha", "project_id": "p1"},
                    {"workspace_id": "beta", "project_id": "p2"},
                ]
            }
        )
        registry = load_workspaces(self.config)
        with self.assertRaises(WorkspaceUnknown):
            registry.get("gamma")

    def test_disabled_workspace_refuses_distinctly(self):
        self.write(
            {"workspaces": [{"workspace_id": "w1", "project_id": "p1", "enabled": False}]}
        )
        registry = load_workspaces(self.config)
        with self.assertRaises(WorkspaceDisabled):
            registry.get("w1")
        # ...but it is still findable, because a *stored* active id pointing at a
        # disabled workspace has to be renderable rather than raising on a read.
        self.assertIsNotNone(registry.find("w1"))
        self.assertEqual(registry.enabled_workspaces(), ())

    # -- publication ---------------------------------------------------------

    def test_published_shape_carries_no_path(self):
        workspace = Workspace(
            workspace_id="w1", display_name="W", project_id="p1", notes="n"
        )
        payload = workspace.to_dict()
        serialized = json.dumps(payload)
        for leak in ("/home", "/etc", "root", "path", "directory"):
            self.assertNotIn(leak, serialized)

    def test_valid_workspace_id_grammar(self):
        self.assertTrue(valid_workspace_id("a-b_c9"))
        self.assertFalse(valid_workspace_id("A"))
        self.assertFalse(valid_workspace_id("a b"))
        self.assertFalse(valid_workspace_id("a/b"))
        self.assertFalse(valid_workspace_id(""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
