"""Descriptor-relative containment: a link anywhere is a refusal, not a redirect.

The containment invariant PR2 claims is that an approved role reaches exactly one
file inside one approved root. A pathname walk cannot enforce that on its own —
it checks one view of the filesystem and then opens another, and the gap between
them is usable.

So resolution opens a descriptor on the verified root and opens every component
below it *relative to the one above*, with ``O_NOFOLLOW`` throughout. These tests
attack that from every direction a link can be introduced, and assert the same
two things each time: the request is refused, and **the file outside the root is
byte-identical afterwards** — which is what distinguishes containment from a
merely unhelpful error.
"""

from __future__ import annotations

import os
import unittest

from ._mind_doubles import MindHarness

OUTSIDE = "outside the root, and must stay that way\n"


@unittest.skipUnless(hasattr(os, "symlink"), "the platform has no symlinks")
class Containment(MindHarness):
    grant_vault = True

    def setUp(self) -> None:
        super().setUp()
        self.outside_dir = self.home / "outside"
        self.outside_dir.mkdir()
        self.outside_file = self.outside_dir / "SECRET.md"
        self.outside_file.write_text(OUTSIDE, encoding="utf-8")

    def link(self, source, destination):
        try:
            os.symlink(source, destination)
        except (OSError, NotImplementedError):  # pragma: no cover
            self.skipTest("this platform cannot create a symlink")

    def refused(self, scope="project", role="status"):
        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document(scope, role)
        self.assertEqual(caught.exception.code, "mind_role_unavailable")
        # The point of containment: nothing outside was read *or* written.
        self.assertEqual(self.outside_file.read_text(encoding="utf-8"), OUTSIDE)
        return caught.exception


class TheSupportGate(MindHarness):
    """No silent fallback to a racy pathname walk."""

    grant_vault = True

    def test_this_platform_supports_safe_resolution(self):
        """The premise of every other test in this file."""
        from cofferdam.workstation.mind.documents import descriptor_resolution_supported

        self.assertTrue(descriptor_resolution_supported())

    def test_without_the_primitives_resolution_refuses_rather_than_degrades(self):
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        original = documents_module._DESCRIPTOR_RESOLUTION_SUPPORTED
        documents_module._DESCRIPTOR_RESOLUTION_SUPPORTED = False
        try:
            with self.assertRaises(MindError) as caught:
                self.mind.read_document("project", "status")
            self.assertEqual(caught.exception.code, "mind_resolution_unsupported")
            with self.assertRaises(MindError):
                self.mind.create_proposal(
                    scope="project", role="status", content="x\n", reason="y"
                )
        finally:
            documents_module._DESCRIPTOR_RESOLUTION_SUPPORTED = original

        self.assertEqual(self.project_text("STATUS.md"), "# Status\n\noriginal\n")


class FinalComponent(Containment):
    def test_the_document_itself_is_a_symlink(self):
        (self.project_root / "STATUS.md").unlink()
        self.link(self.outside_file, self.project_root / "STATUS.md")
        self.activate()
        self.refused()

    def test_a_symlinked_document_cannot_be_written_through(self):
        """The apply must not follow it either."""
        from cofferdam.workstation.mind.errors import MindError

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        (self.project_root / "STATUS.md").unlink()
        self.link(self.outside_file, self.project_root / "STATUS.md")

        with self.assertRaises(MindError):
            self.mind.accept_proposal(created["proposal_id"])
        self.assertEqual(self.outside_file.read_text(encoding="utf-8"), OUTSIDE)

    def test_a_dangling_symlink_is_refused(self):
        (self.project_root / "STATUS.md").unlink()
        self.link(self.home / "nothing-here.md", self.project_root / "STATUS.md")
        self.activate()
        self.refused()


class IntermediateComponent(Containment):
    """The race the pathname walk could not close."""

    def setUp(self) -> None:
        super().setUp()
        (self.outside_dir / "PLAN.md").write_text(OUTSIDE, encoding="utf-8")

    def test_a_symlinked_intermediate_directory_is_refused(self):
        self.link(self.outside_dir, self.project_root / "memory")
        self.write_workspaces(documents={"plan": "memory/PLAN.md"})
        self.build_services()
        self.activate()
        self.refused(role="plan")

    def test_a_symlinked_directory_deeper_in_is_refused(self):
        (self.project_root / "memory").mkdir()
        self.link(self.outside_dir, self.project_root / "memory" / "notes")
        self.write_workspaces(documents={"plan": "memory/notes/PLAN.md"})
        self.build_services()
        self.activate()
        self.refused(role="plan")

    def test_an_intermediate_swapped_between_resolution_and_use_is_refused(self):
        """The window a pathname walk leaves open, driven deterministically.

        The directory is real and ordinary when resolution begins, and is
        replaced by a link to somewhere outside the root at the exact moment the
        walk reaches it. A resolver that checked names and then opened them would
        follow the replacement; opening each component relative to the verified
        one above it means the kernel refuses.
        """
        from cofferdam.workstation.mind import documents as documents_module
        from cofferdam.workstation.mind.errors import MindError

        real_directory = self.project_root / "memory"
        real_directory.mkdir()
        (real_directory / "PLAN.md").write_text("inside the root\n", encoding="utf-8")
        self.write_workspaces(documents={"plan": "memory/PLAN.md"})
        self.build_services()
        self.activate()

        real_open = documents_module.os.open
        swapped = []

        def swap_then_open(path, flags, *args, **kwargs):
            # Fire once, as the walk is about to open the root descriptor, so
            # `memory` is a symlink by the time the walk asks for it.
            if not swapped and kwargs.get("dir_fd") is None:
                swapped.append(True)
                os.rename(real_directory, self.project_root / "memory-real")
                os.symlink(self.outside_dir, real_directory)
            return real_open(path, flags, *args, **kwargs)

        documents_module.os.open = swap_then_open
        try:
            with self.assertRaises(MindError) as caught:
                self.mind.read_document("project", "plan")
        finally:
            documents_module.os.open = real_open

        self.assertTrue(swapped, "the swap never fired; the test proved nothing")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")
        self.assertEqual(self.outside_file.read_text(encoding="utf-8"), OUTSIDE)

    def test_an_ordinary_nested_document_still_works(self):
        """The control. A resolver that refused everything would pass the rest."""
        (self.project_root / "memory").mkdir()
        (self.project_root / "memory" / "PLAN.md").write_text("nested\n", encoding="utf-8")
        self.write_workspaces(documents={"plan": "memory/PLAN.md"})
        self.build_services()
        self.activate()

        self.assertEqual(self.mind.read_document("project", "plan")["content"], "nested\n")
        created = self.mind.create_proposal(
            scope="project", role="plan", content="nested and rewritten\n", reason="y"
        )
        self.assertEqual(self.mind.accept_proposal(created["proposal_id"])["state"], "applied")
        self.assertEqual(
            (self.project_root / "memory" / "PLAN.md").read_text(encoding="utf-8"),
            "nested and rewritten\n",
        )

    def test_an_intermediate_that_is_a_regular_file_is_refused(self):
        (self.project_root / "memory").write_text("not a directory\n", encoding="utf-8")
        self.write_workspaces(documents={"plan": "memory/PLAN.md"})
        self.build_services()
        self.activate()
        self.refused(role="plan")


class RootReplacement(Containment):
    def test_a_symlinked_project_root_is_refused(self):
        import json

        real = self.home / "elsewhere-project"
        real.mkdir()
        (real / "STATUS.md").write_text(OUTSIDE, encoding="utf-8")
        linked = self.home / "linked-project"
        self.link(real, linked)
        (self.config.config_dir / "task-projects.json").write_text(
            json.dumps(
                {"projects": [{"project_id": "demo", "root": str(linked),
                               "adapters": ["validation"]}]}
            ),
            encoding="utf-8",
        )
        self.build_services()
        self.activate()

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document("project", "status")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")
        self.assertEqual((real / "STATUS.md").read_text(encoding="utf-8"), OUTSIDE)

    def test_a_symlinked_vault_root_is_refused(self):
        real = self.home / "elsewhere-vault"
        real.mkdir()
        (real / "USER.md").write_text(OUTSIDE, encoding="utf-8")
        linked = self.home / "linked-vault"
        self.link(real, linked)
        self.write_grant(root=linked, documents={"user": "USER.md"})
        self.build_services()

        from cofferdam.workstation.mind.errors import MindError

        with self.assertRaises(MindError) as caught:
            self.mind.read_document("global", "user")
        self.assertEqual(caught.exception.code, "mind_role_unavailable")
        self.assertEqual((real / "USER.md").read_text(encoding="utf-8"), OUTSIDE)


class WriteStaysInsideTheVerifiedParent(MindHarness):
    """The temporary file and the rename never leave the directory that was opened."""

    grant_vault = True

    def test_the_temporary_file_is_created_in_the_target_directory(self):
        from cofferdam.workstation.mind import documents as documents_module

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )

        real_open = documents_module.os.open
        creations = []

        def watched(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                # Relative name plus a directory descriptor, never an absolute
                # path — which is what "inside the verified parent" means.
                creations.append((path, kwargs.get("dir_fd")))
            return real_open(path, flags, *args, **kwargs)

        documents_module.os.open = watched
        try:
            self.mind.accept_proposal(created["proposal_id"])
        finally:
            documents_module.os.open = real_open

        self.assertEqual(len(creations), 1)
        name, dir_fd = creations[0]
        self.assertIsNotNone(dir_fd)
        self.assertFalse(os.path.isabs(name))
        self.assertNotIn("/", name)
        self.assertTrue(name.startswith(".cofferdam-mind-"))

    def test_the_rename_uses_directory_descriptors_on_both_sides(self):
        from cofferdam.workstation.mind import documents as documents_module

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )

        real_rename = documents_module.os.rename
        seen = []

        def watched(src, dst, **kwargs):
            seen.append((src, dst, kwargs.get("src_dir_fd"), kwargs.get("dst_dir_fd")))
            return real_rename(src, dst, **kwargs)

        documents_module.os.rename = watched
        try:
            self.mind.accept_proposal(created["proposal_id"])
        finally:
            documents_module.os.rename = real_rename

        self.assertEqual(len(seen), 1)
        src, dst, src_fd, dst_fd = seen[0]
        self.assertFalse(os.path.isabs(src))
        self.assertFalse(os.path.isabs(dst))
        self.assertEqual(dst, "STATUS.md")
        self.assertIsNotNone(src_fd)
        # The same descriptor on both sides: a rename cannot cross out of the
        # directory that was verified, so it also cannot cross a filesystem.
        self.assertEqual(src_fd, dst_fd)

    def test_mode_and_durability_are_preserved(self):
        import stat as stat_module

        self.activate()
        target = self.project_root / "STATUS.md"
        os.chmod(target, 0o640)
        before = stat_module.S_IMODE(os.stat(target).st_mode)

        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )
        self.mind.accept_proposal(created["proposal_id"])

        self.assertEqual(stat_module.S_IMODE(os.stat(target).st_mode), before)
        self.assertEqual(target.read_text(encoding="utf-8"), "rewritten\n")
        self.assertEqual(
            [p.name for p in self.project_root.iterdir() if p.name.startswith(".")], []
        )

    def test_the_content_is_fsynced_before_the_rename(self):
        from cofferdam.workstation.mind import documents as documents_module

        self.activate()
        created = self.mind.create_proposal(
            scope="project", role="status", content="rewritten\n", reason="y"
        )

        order = []
        real_fsync = documents_module.os.fsync
        real_rename = documents_module.os.rename

        def watched_fsync(fd):
            order.append("fsync")
            return real_fsync(fd)

        def watched_rename(*args, **kwargs):
            order.append("rename")
            return real_rename(*args, **kwargs)

        documents_module.os.fsync = watched_fsync
        documents_module.os.rename = watched_rename
        try:
            self.mind.accept_proposal(created["proposal_id"])
        finally:
            documents_module.os.fsync = real_fsync
            documents_module.os.rename = real_rename

        self.assertEqual(order[:2], ["fsync", "rename"])
        # And the directory is fsynced after, so the rename itself is durable.
        self.assertEqual(order[-1], "fsync")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
