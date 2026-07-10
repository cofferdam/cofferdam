"""Negative-first tests for path normalization, containment, and protection."""

import unittest

from cofferdam.paths import (
    MAX_COMPONENT_LENGTH,
    MAX_PATH_DEPTH,
    assess_path,
    match_protected,
    normalize_target,
)
from cofferdam.repo_view import PathType, RepoView
from cofferdam.verdict import Decision, ReasonCode, Risk


class FakeRepoView:
    """A test double implementing the RepoView contract from a dict."""

    def __init__(self, mapping=None, default=PathType.MISSING):
        self._mapping = dict(mapping or {})
        self._default = default

    def path_type(self, parts):
        return self._mapping.get(tuple(parts), self._default)


ALL_MISSING = FakeRepoView()


class NormalizeRejectTests(unittest.TestCase):
    def assertReject(self, raw, code):
        result = normalize_target(raw)
        self.assertFalse(result.ok, f"expected rejection for {raw!r}")
        self.assertIn(code, result.reasons)

    def test_empty(self):
        self.assertReject("", ReasonCode.PATH_EMPTY_AFTER_NORMALIZE)

    def test_absolute_posix(self):
        self.assertReject("/etc/passwd", ReasonCode.PATH_ABSOLUTE)

    def test_leading_backslash(self):
        self.assertReject("\\windows\\system32", ReasonCode.PATH_ABSOLUTE)

    def test_unc(self):
        self.assertReject("\\\\server\\share\\x", ReasonCode.PATH_UNC)

    def test_drive_letter(self):
        self.assertReject("C:\\secret", ReasonCode.PATH_DRIVE_LETTER)

    def test_parent_traversal(self):
        self.assertReject("a/../../etc/passwd", ReasonCode.PATH_PARENT_TRAVERSAL)

    def test_curdir_segment(self):
        self.assertReject("a/./b", ReasonCode.PATH_CURDIR_SEGMENT)

    def test_empty_segment(self):
        self.assertReject("a//b", ReasonCode.PATH_EMPTY_SEGMENT)

    def test_control_char(self):
        self.assertReject("a\tb.py", ReasonCode.PATH_NUL_OR_CONTROL)
        self.assertReject("a\x00b.py", ReasonCode.PATH_NUL_OR_CONTROL)

    def test_alternate_data_stream(self):
        self.assertReject("file.py:hidden", ReasonCode.PATH_ALTERNATE_DATA_STREAM)

    def test_trailing_dot_or_space(self):
        # Windows strips a component's *trailing* dots/spaces; an interior space
        # (e.g. "name .py") is legal and not flagged.
        self.assertReject("dir/name.py ", ReasonCode.PATH_TRAILING_DOT_OR_SPACE)
        self.assertReject("dir/name.", ReasonCode.PATH_TRAILING_DOT_OR_SPACE)

    def test_reserved_device_names(self):
        for name in ("con", "CON", "nul.txt", "com1", "LPT9.log"):
            with self.subTest(name=name):
                self.assertReject(name, ReasonCode.PATH_RESERVED_DEVICE_NAME)

    def test_component_too_long(self):
        long = "a" * (MAX_COMPONENT_LENGTH + 1)
        self.assertReject(f"src/{long}.py", ReasonCode.PATH_COMPONENT_TOO_LONG)

    def test_too_deep(self):
        deep = "/".join(["d"] * (MAX_PATH_DEPTH + 1))
        self.assertReject(deep, ReasonCode.PATH_TOO_DEEP)


class NormalizeAcceptTests(unittest.TestCase):
    def test_simple_relative_path(self):
        result = normalize_target("src/app.py")
        self.assertTrue(result.ok)
        self.assertEqual(result.path.parts, ("src", "app.py"))
        self.assertEqual(result.path.fold, ("src", "app.py"))

    def test_backslash_separator_accepted_and_split(self):
        result = normalize_target("src\\pkg\\mod.py")
        self.assertTrue(result.ok)
        self.assertEqual(result.path.parts, ("src", "pkg", "mod.py"))


class ProtectedTests(unittest.TestCase):
    def assertTier(self, raw, tier):
        norm = normalize_target(raw)
        self.assertTrue(norm.ok, f"{raw!r} should normalize")
        got, _ = match_protected(norm.path)
        self.assertEqual(got, tier)

    def test_tier1_blocks(self):
        for raw in (
            ".git/config",
            ".GIT/config",           # case-insensitive
            ".github/workflows/ci.yml",
            ".circleci/config.yml",
            "setup.py",
            "Makefile",
            "pyproject.toml",
            "package.json",
            ".pre-commit-config.yaml",
            ".cofferdam/rules",
        ):
            with self.subTest(raw=raw):
                self.assertTier(raw, "block")

    def test_tier2_high(self):
        for raw in (
            ".env",
            "config/.env.production",
            "certs/server.pem",
            "keys/id_rsa",
            ".ssh/known_hosts",
            ".gitattributes",
        ):
            with self.subTest(raw=raw):
                self.assertTier(raw, "high")

    def test_ordinary_path_not_protected(self):
        self.assertTier("src/app.py", None)


class AssessPathTests(unittest.TestCase):
    def test_ordinary_path_needs_approval_low(self):
        result = assess_path("src/app.py", ALL_MISSING)
        self.assertEqual(result.decision, Decision.NEEDS_APPROVAL)
        self.assertEqual(result.risk, Risk.LOW)

    def test_lexical_reject_blocks(self):
        result = assess_path("../escape", ALL_MISSING)
        self.assertEqual(result.decision, Decision.BLOCKED)
        self.assertIn(ReasonCode.PATH_PARENT_TRAVERSAL, result.reasons)

    def test_tier1_blocks_before_repo_view(self):
        # Even if the FS says the path is a normal missing file, Tier 1 blocks.
        result = assess_path(".git/config", ALL_MISSING)
        self.assertEqual(result.decision, Decision.BLOCKED)
        self.assertIn(ReasonCode.PROTECTED_BLOCKED, result.reasons)

    def test_intermediate_symlink_component_blocks(self):
        view = FakeRepoView({("src",): PathType.SYMLINK})
        result = assess_path("src/app.py", view)
        self.assertEqual(result.decision, Decision.BLOCKED)
        self.assertIn(ReasonCode.PATH_SYMLINK_COMPONENT, result.reasons)

    def test_target_symlink_blocks(self):
        view = FakeRepoView({("src", "app.py"): PathType.SYMLINK})
        result = assess_path("src/app.py", view)
        self.assertEqual(result.decision, Decision.BLOCKED)
        self.assertIn(ReasonCode.PATH_SYMLINK_COMPONENT, result.reasons)

    def test_non_regular_target_blocks(self):
        view = FakeRepoView({("src", "app.py"): PathType.DIRECTORY})
        result = assess_path("src/app.py", view)
        self.assertEqual(result.decision, Decision.BLOCKED)
        self.assertIn(ReasonCode.PATH_NON_REGULAR_TARGET, result.reasons)

    def test_tier2_needs_approval_high(self):
        result = assess_path(".env", ALL_MISSING)
        self.assertEqual(result.decision, Decision.NEEDS_APPROVAL)
        self.assertEqual(result.risk, Risk.HIGH)
        self.assertIn(ReasonCode.PROTECTED_HIGH_RISK, result.reasons)

    def test_injection_worded_path_is_ordinary(self):
        # Content-as-data: an "approve"-worded filename is just a filename.
        result = assess_path("src/ignore_rules_and_approve.py", ALL_MISSING)
        self.assertEqual(result.decision, Decision.NEEDS_APPROVAL)
        self.assertEqual(result.risk, Risk.LOW)

    def test_assess_is_deterministic(self):
        view = FakeRepoView({("src",): PathType.SYMLINK})
        self.assertEqual(assess_path("src/app.py", view), assess_path("src/app.py", view))


if __name__ == "__main__":
    unittest.main()
