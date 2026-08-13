"""M2J PR4 — the read service: resolution, redaction authority, type boundary.

This file tests the half of PR4 that decides *whether* a read may be answered
and *what object* is allowed to become a response. The HTTP layers above it are
tested in their own files; the property proved here is that neither of them can
reach past this module to a `LocalContextPack`.
"""

from __future__ import annotations

import json
import unittest

from ._mind_doubles import PROJECT_ID, WORKSPACE_ID
from .test_context_builder import ContextHarness


class ReadHarness(ContextHarness):
    """A real registry, a real workspace, a real builder, a real projector."""

    def _registry(self):
        from cofferdam.workstation.tasks.projects import load_projects

        return load_projects(self.config, ("validation",))

    def service(self, **kwargs):
        from cofferdam.workstation.projectcontext import ProjectContextService

        return ProjectContextService(
            config=self.config,
            workspaces=self.workspaces,
            projects=self.workspaces.workspaces and self._registry(),
            builder=self.builder,
            **kwargs,
        )

    def read(self, project_id=PROJECT_ID):
        return self.service().project_context(project_id)

    def payload(self, project_id=PROJECT_ID):
        from cofferdam.workstation.projectcontext import serialize_project_context

        return serialize_project_context(self.read(project_id))

    def blob(self, project_id=PROJECT_ID):
        return json.dumps(self.payload(project_id), ensure_ascii=False, sort_keys=True)


class Resolution(ReadHarness):
    """A project id resolves to one workspace, or it is refused with a reason."""

    def test_the_active_project_resolves(self):
        self.activate()
        resolved = self.read()
        self.assertEqual(resolved.project_id, PROJECT_ID)
        self.assertEqual(resolved.workspace_id, WORKSPACE_ID)

    def test_an_unknown_project_is_refused(self):
        from cofferdam.workstation.projectcontext import (
            REASON_PROJECT_NOT_FOUND,
            ProjectContextUnavailable,
        )

        self.activate()
        with self.assertRaises(ProjectContextUnavailable) as caught:
            self.read("no-such-project")
        self.assertEqual(caught.exception.reason, REASON_PROJECT_NOT_FOUND)

    def test_no_active_workspace_is_refused(self):
        from cofferdam.workstation.projectcontext import (
            REASON_WORKSPACE_NOT_ACTIVE,
            ProjectContextUnavailable,
        )

        with self.assertRaises(ProjectContextUnavailable) as caught:
            self.read()
        self.assertEqual(caught.exception.reason, REASON_WORKSPACE_NOT_ACTIVE)

    def test_a_path_shaped_project_id_is_refused_before_any_lookup(self):
        from cofferdam.workstation.projectcontext import (
            REASON_INVALID_PROJECT_ID,
            ProjectContextUnavailable,
        )

        self.activate()
        for hostile in (
            "../../etc/passwd",
            "/home/someone/project",
            "cofferdam/../other",
            "..",
            ".hidden",
            "a" * 200,
            "proj ect",
            "proj%2Fect",
            "",
            None,
            42,
        ):
            with self.subTest(value=hostile):
                with self.assertRaises(ProjectContextUnavailable) as caught:
                    self.read(hostile)
                self.assertEqual(caught.exception.reason, REASON_INVALID_PROJECT_ID)

    def test_a_refusal_message_carries_no_path(self):
        from cofferdam.workstation.projectcontext import ProjectContextUnavailable

        for value in ("no-such-project", "../../etc/passwd"):
            with self.assertRaises(ProjectContextUnavailable) as caught:
                self.read(value)
            message = str(caught.exception)
            for fragment in (str(self.home), str(self.project_root), "/home/", "/tmp/"):
                self.assertNotIn(fragment, message)


class CallerAuthority(ReadHarness):
    """The caller names a project. It cannot name anything else."""

    def test_the_only_input_is_a_project_id(self):
        import inspect

        from cofferdam.workstation.projectcontext import ProjectContextService

        signature = inspect.signature(ProjectContextService.project_context)
        self.assertEqual(list(signature.parameters), ["self", "project_id"])

    def test_the_service_exposes_no_policy_or_root_parameter(self):
        import inspect

        from cofferdam.workstation.projectcontext import ProjectContextService

        source = inspect.getsource(ProjectContextService.project_context)
        # The method *does* pass `redaction=` — that is the host-owned
        # environment being supplied, which is the requirement rather than a
        # violation. What must not appear is a caller-reachable selector.
        for forbidden in ("policy_id=", "allowlist=", "roles=", "source_ref="):
            self.assertNotIn(forbidden, source)


class RedactionAuthority(ReadHarness):
    """The environment is host-owned, and `.none()` is not the production path."""

    def test_the_environment_is_built_from_host_configuration(self):
        self.activate()
        environment = self.service()._redaction_environment(self.project_root)
        literals = environment.literals
        self.assertIn(str(self.project_root), literals)
        self.assertIn(str(self.home), literals)
        self.assertIn(str(self.home / "slots" / "a"), literals)
        self.assertIn(str(self.home / "slots" / "b"), literals)

    def test_the_vault_root_is_included_when_granted(self):
        self.activate()
        environment = self.service()._redaction_environment(self.project_root)
        self.assertIn(str(self.vault_root), environment.literals)

    def test_the_production_path_never_uses_the_empty_environment(self):
        """Checked on the parsed tree, not on the text.

        The module says `.none()` twice in prose in order to record that it is
        not used, so a substring scan would fail on its own documentation. An AST
        walk asks the only question that matters: is it ever *called*?
        """
        import ast
        from pathlib import Path

        from cofferdam.workstation import projectcontext as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        called = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "none"
        ]
        self.assertEqual(called, [])

    def test_no_caller_value_reaches_the_environment(self):
        """A hostile project id cannot become a redaction literal."""
        self.activate()
        environment = self.service()._redaction_environment(self.project_root)
        for literal in environment.literals:
            self.assertNotIn("no-such", literal)


class TypeBoundary(ReadHarness):
    """Only a `CloudContextProjection` may be serialized."""

    def test_the_read_returns_a_projection_not_a_pack(self):
        from cofferdam.workstation.context.models import LocalContextPack
        from cofferdam.workstation.context.projection import CloudContextProjection

        self.activate()
        resolved = self.read()
        self.assertIsInstance(resolved.projection, CloudContextProjection)
        self.assertNotIsInstance(resolved.projection, LocalContextPack)

    def test_a_local_pack_handed_to_the_serializer_is_refused(self):
        from cofferdam.workstation.projectcontext import (
            ResolvedContext,
            ProjectContextUnavailable,
            serialize_project_context,
        )

        self.activate()
        pack = self.builder.build_without_message()
        smuggled = ResolvedContext(
            workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, projection=pack
        )
        with self.assertRaises(ProjectContextUnavailable):
            serialize_project_context(smuggled)

    def test_a_bare_pack_is_refused(self):
        from cofferdam.workstation.projectcontext import (
            ProjectContextUnavailable,
            serialize_project_context,
        )

        self.activate()
        with self.assertRaises(ProjectContextUnavailable):
            serialize_project_context(self.builder.build_without_message())

    def test_the_payload_carries_the_policy_that_produced_it(self):
        self.activate()
        payload = self.payload()
        self.assertEqual(
            payload["context"]["policy_id"], "project_context_external_v1"
        )
        self.assertEqual(payload["version"], 1)


class WhatLeaves(ReadHarness):
    """The complete serialized payload, not the Python object."""

    def test_project_material_survives(self):
        self.activate()
        self.workspaces.set_objective("ship the read surface")
        blob = self.blob()
        self.assertIn("ship the read surface", blob)

    def test_all_four_global_mind_sentinels_are_absent(self):
        """No Global Mind **content**, in the complete serialized payload."""
        self.activate()
        blob = self.blob()
        for sentinel in ("SENTINEL-USER-IDENTITY", "SENTINEL-CROSS-PROJECT",
                         "Direct, no filler", "Python, stdlib first"):
            self.assertNotIn(sentinel, blob)

    def test_no_projected_part_carries_a_global_reference(self):
        self.activate()
        payload = self.payload()
        for part in payload["context"]["parts"]:
            self.assertFalse(part["source_ref"].startswith("global:"))

    def test_an_excluded_global_role_is_named_in_the_omissions_by_design(self):
        """The role name is code-owned vocabulary; the document is not there.

        "Nothing disappears silently" is the guarantee, so an excluded source
        appears as a reason row. `global:preferences` is a role this repository
        documents publicly — the omission says a document was refused, and
        carries none of it.
        """
        self.activate()
        payload = self.payload()
        omitted = {row["source_ref"] for row in payload["context"]["omissions"]}
        self.assertTrue(any(ref.startswith("global:") for ref in omitted))
        for row in payload["context"]["omissions"]:
            self.assertNotIn("text", row)
            self.assertEqual(row["reason"], row["reason"].lower())

    def test_there_is_no_current_message_anywhere(self):
        self.activate()
        blob = self.blob()
        self.assertNotIn("user:current_message", blob)
        self.assertNotIn("user_instruction", blob)

    def test_unsafe_working_context_fields_are_absent(self):
        self.activate()
        self.workspaces.set_objective("visible")
        blob = self.blob()
        for forbidden in ("active_task", "delegated_worker", "latest_evidence_ref",
                          "objective_set_at", "objective_source", "revision"):
            self.assertNotIn(forbidden, blob)

    def test_design_is_absent(self):
        self.activate()
        self.assertNotIn("design", self.blob())

    def test_no_filesystem_path_survives(self):
        self.activate()
        blob = self.blob()
        for fragment in (str(self.home), str(self.project_root), str(self.vault_root),
                         "/home/", "/tmp/", "slots/a", "slots/b", ".obsidian"):
            self.assertNotIn(fragment, blob)

    def test_no_part_metadata_carries_fields_or_a_heading(self):
        self.activate()
        payload = self.payload()
        for part in payload["context"]["parts"]:
            self.assertNotIn("fields", part)
            self.assertNotIn("heading", part)
            self.assertNotIn("section", part)


class SerializedBound(ReadHarness):
    """The HTTP body is bounded, and the bound is not the content bound."""

    def test_the_ceiling_is_larger_than_the_projection_budget(self):
        from cofferdam.workstation.context.projection import (
            DEFAULT_PROJECTION_BUDGET_BYTES,
        )
        from cofferdam.workstation.projectcontext import MAX_SERIALIZED_RESPONSE_BYTES

        self.assertGreater(
            MAX_SERIALIZED_RESPONSE_BYTES, DEFAULT_PROJECTION_BUDGET_BYTES
        )

    def test_an_ordinary_payload_is_far_below_the_ceiling(self):
        from cofferdam.workstation.projectcontext import (
            MAX_SERIALIZED_RESPONSE_BYTES,
            serialized_size,
        )

        self.activate()
        self.assertLess(
            serialized_size(self.payload()), MAX_SERIALIZED_RESPONSE_BYTES
        )

    def test_worst_case_escaping_stays_under_the_ceiling(self):
        """Content that escapes to six bytes per character, at the full budget."""
        from cofferdam.workstation.projectcontext import (
            MAX_SERIALIZED_RESPONSE_BYTES,
            serialized_size,
        )

        self.activate()
        for name in ("STATUS.md", "ROADMAP.md", "DECISIONS.md"):
            (self.project_root / name).write_text(
                "# Heading\n\n" + ("" * 20000) + "\n", encoding="utf-8"
            )
        size = serialized_size(self.payload())
        self.assertLess(size, MAX_SERIALIZED_RESPONSE_BYTES, f"serialized {size} B")


class NoMutation(ReadHarness):
    """A read is a read, proved after a real read."""

    def test_repeated_reads_change_nothing(self):
        self.activate()
        self.workspaces.set_objective("stay still")
        before_context = self.workspace_store.context(WORKSPACE_ID)
        before_proposals = self.mind_store.counts()
        before_files = {str(path) for path in self.home.rglob("*")}

        for _ in range(3):
            self.payload()

        after_context = self.workspace_store.context(WORKSPACE_ID)
        self.assertEqual(before_context.revision, after_context.revision)
        self.assertEqual(before_context.updated_at, after_context.updated_at)
        self.assertEqual(before_proposals, self.mind_store.counts())
        self.assertEqual(before_files, {str(path) for path in self.home.rglob("*")})

    def test_the_read_module_writes_nothing_and_logs_nothing(self):
        from pathlib import Path

        from cofferdam.workstation import projectcontext as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        # Import lines only. The prose above them names `socket` and `logging`
        # precisely to say the module does not use them, and a scan of the whole
        # file would fail on the sentence that documents the guarantee.
        imports = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        for forbidden in ("socket", "httpx", "requests", "urllib", "subprocess",
                          "sqlite3", "logging", "fastapi"):
            for line in imports:
                self.assertNotIn(forbidden, line)
        for forbidden in ("open(", "write_text(", "mkdir(", "print("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
