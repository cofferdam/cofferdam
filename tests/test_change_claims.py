"""M2K PR1 — adapter-reported change claims and the artifact foundation.

Everything here runs against a throwaway ``COFFERDAM_HOME`` and a synthetic
project. No provider, no model, no network, no shell.

The property most of these tests exist to defend is one sentence: **a claim is
what a worker said, an artifact record is what Cofferdam saw, and no code path
turns the first into the second.**
"""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

try:  # the Actions bridge needs the workstation extras; the Trust Core run has none
    import httpx as _httpx
except ImportError:  # pragma: no cover - exercised on the stdlib-only runner
    _httpx = None

from cofferdam.workstation.tasks.claims import (
    ARTIFACT_ID_PREFIX,
    CLAIM_CREATED,
    CLAIM_DELETED,
    CLAIM_ID_PREFIX,
    CLAIM_MODIFIED,
    CLAIM_OPERATIONS,
    CLAIM_RENAMED,
    MAX_ARTIFACT_READ_BYTES,
    MAX_CLAIMS_PER_OUTCOME,
    MAX_CLAIM_PATH_CHARS,
    MAX_PREVIEW_BYTES,
    REASON_ARTIFACT_MISSING,
    REASON_ARTIFACT_NOT_REGULAR,
    REASON_ARTIFACT_TOO_LARGE,
    REASON_OK,
    REASON_PATH_DENIED_SENSITIVE,
    REASON_PATH_ESCAPE,
    REASON_PATH_INVALID,
    REASON_PREVIEW_UNSUPPORTED_TYPE,
    ArtifactRecord,
    ChangeClaim,
    ClaimPathInvalid,
    ClaimSubmission,
    artifact_digest,
    is_denied_path,
    new_artifact_id,
    new_claim_id,
    normalize_claim_path,
    observe_artifact,
    valid_artifact_id,
    valid_claim_id,
    validate_submission,
)
from cofferdam.workstation.tasks.models import (
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_GIT_OBSERVED,
    EVIDENCE_OS_OBSERVED,
    VERIFIED_EVIDENCE_SOURCES,
)


class PathAuthorityTests(unittest.TestCase):
    """Lexical refusals. Nothing here touches a filesystem."""

    def test_a_plain_relative_path_is_accepted(self):
        self.assertEqual(normalize_claim_path("src/foo.py"), "src/foo.py")
        self.assertEqual(normalize_claim_path("README.md"), "README.md")

    def test_an_absolute_path_is_an_escape(self):
        with self.assertRaises(ClaimPathInvalid) as caught:
            normalize_claim_path("/etc/passwd")
        self.assertEqual(caught.exception.reason, REASON_PATH_ESCAPE)

    def test_a_home_relative_path_is_an_escape(self):
        with self.assertRaises(ClaimPathInvalid) as caught:
            normalize_claim_path("~/secrets")
        self.assertEqual(caught.exception.reason, REASON_PATH_ESCAPE)

    def test_dot_dot_is_an_escape_anywhere_in_the_path(self):
        for candidate in ("../x", "a/../../b", "a/..", ".."):
            with self.assertRaises(ClaimPathInvalid) as caught:
                normalize_claim_path(candidate)
            self.assertEqual(caught.exception.reason, REASON_PATH_ESCAPE, candidate)

    def test_a_windows_drive_letter_is_an_escape(self):
        with self.assertRaises(ClaimPathInvalid) as caught:
            normalize_claim_path("C:/Users/x")
        self.assertEqual(caught.exception.reason, REASON_PATH_ESCAPE)

    def test_a_backslash_is_refused_rather_than_reinterpreted(self):
        with self.assertRaises(ClaimPathInvalid) as caught:
            normalize_claim_path("src\\foo.py")
        self.assertEqual(caught.exception.reason, REASON_PATH_INVALID)

    def test_nul_and_control_characters_are_refused(self):
        for candidate in ("src/\x00foo", "src/\nfoo", "src/\tfoo", "a\x07b"):
            with self.assertRaises(ClaimPathInvalid) as caught:
                normalize_claim_path(candidate)
            self.assertEqual(caught.exception.reason, REASON_PATH_INVALID, repr(candidate))

    def test_empty_and_non_text_paths_are_refused(self):
        for candidate in ("", None, 7, b"src/foo.py", [], {}):
            with self.assertRaises(ClaimPathInvalid):
                normalize_claim_path(candidate)

    def test_an_over_long_path_is_refused_rather_than_truncated(self):
        with self.assertRaises(ClaimPathInvalid) as caught:
            normalize_claim_path("a/" * MAX_CLAIM_PATH_CHARS)
        self.assertEqual(caught.exception.reason, REASON_PATH_INVALID)

    def test_an_over_long_single_segment_is_refused(self):
        with self.assertRaises(ClaimPathInvalid):
            normalize_claim_path("src/" + ("x" * 400))

    def test_empty_and_dot_segments_are_refused(self):
        for candidate in ("src//foo", "./foo", "src/./foo", "/", "src/"):
            with self.assertRaises(ClaimPathInvalid):
                normalize_claim_path(candidate)

    def test_a_refusal_never_echoes_the_offending_path(self):
        """A message that repeats the input describes the host one try at a time."""
        try:
            normalize_claim_path("../../etc/shadow")
        except ClaimPathInvalid as rejection:
            self.assertNotIn("etc", str(rejection))
            self.assertNotIn("shadow", str(rejection))
            self.assertEqual(str(rejection), REASON_PATH_ESCAPE)


class SubmissionTests(unittest.TestCase):
    def test_the_operation_vocabulary_is_closed(self):
        self.assertEqual(
            set(CLAIM_OPERATIONS),
            {CLAIM_CREATED, CLAIM_MODIFIED, CLAIM_DELETED, CLAIM_RENAMED},
        )

    def test_an_unknown_operation_is_refused(self):
        for operation in ("touched", "edited", "", "MODIFIED", None, 3):
            with self.assertRaises(ClaimPathInvalid):
                validate_submission(ClaimSubmission(operation=operation, path="a.py"))

    def test_a_rename_carries_two_validated_paths_in_their_own_fields(self):
        operation, path, to_path, _ = validate_submission(
            ClaimSubmission(operation=CLAIM_RENAMED, path="a.py", to_path="b.py")
        )
        self.assertEqual((operation, path, to_path), (CLAIM_RENAMED, "a.py", "b.py"))

    def test_a_rename_destination_is_validated_like_any_other_path(self):
        with self.assertRaises(ClaimPathInvalid) as caught:
            validate_submission(
                ClaimSubmission(operation=CLAIM_RENAMED, path="a.py", to_path="../b.py")
            )
        self.assertEqual(caught.exception.reason, REASON_PATH_ESCAPE)

    def test_a_rename_without_a_destination_is_refused(self):
        with self.assertRaises(ClaimPathInvalid):
            validate_submission(ClaimSubmission(operation=CLAIM_RENAMED, path="a.py"))

    def test_a_destination_on_a_non_rename_is_refused(self):
        with self.assertRaises(ClaimPathInvalid):
            validate_submission(
                ClaimSubmission(operation=CLAIM_MODIFIED, path="a.py", to_path="b.py")
            )

    def test_the_adapter_label_is_bounded_untrusted_metadata(self):
        _, _, _, label = validate_submission(
            ClaimSubmission(operation=CLAIM_MODIFIED, path="a.py", label="x" * 5000)
        )
        self.assertLessEqual(len(label), 120)

    def test_a_submission_has_nowhere_to_put_authority_fields(self):
        """The strongest form of the rule: the fields do not exist."""
        fields = set(ClaimSubmission.__dataclass_fields__)
        for forbidden in (
            "claim_id",
            "artifact_id",
            "source",
            "digest",
            "verified",
            "root",
            "project_root",
            "command",
            "argv",
            "shell",
            "test_command",
            "validation_command",
        ):
            self.assertNotIn(forbidden, fields)

    def test_a_non_submission_object_is_refused(self):
        class Lookalike:
            operation = CLAIM_MODIFIED
            path = "a.py"
            to_path = None
            label = None

        with self.assertRaises(ClaimPathInvalid):
            validate_submission(Lookalike())


class DenyListTests(unittest.TestCase):
    """Synthetic names only — nothing here names a real host path."""

    def test_credential_file_names_are_denied(self):
        for candidate in (
            ".env",
            "svc/.env",
            ".env.production",
            "deploy/.netrc",
            "id_rsa",
            "keys/id_ed25519",
            "app/credentials.json",
            "secrets.json",
            ".git-credentials",
            ".npmrc",
        ):
            self.assertTrue(is_denied_path(candidate), candidate)

    def test_private_key_suffixes_are_denied(self):
        for candidate in ("certs/server.pem", "a/b/private.key", "x.p12", "y.pfx"):
            self.assertTrue(is_denied_path(candidate), candidate)

    def test_credential_directories_deny_their_whole_subtree(self):
        for candidate in (
            ".ssh/known_hosts",
            ".aws/config",
            "secrets/anything.txt",
            ".gnupg/x",
        ):
            self.assertTrue(is_denied_path(candidate), candidate)

    def test_the_deny_check_is_case_insensitive(self):
        for candidate in (".ENV", "Certs/Server.PEM", ".SSH/x", "ID_RSA"):
            self.assertTrue(is_denied_path(candidate), candidate)

    def test_ordinary_source_files_are_not_denied(self):
        for candidate in (
            "src/foo.py",
            "README.md",
            "docs/env.md",
            "environment.py",
            "keyboard.py",
            "tests/test_secrets_policy.py",
        ):
            self.assertFalse(is_denied_path(candidate), candidate)


class IdentityTests(unittest.TestCase):
    def test_ids_are_prefixed_opaque_and_shaped(self):
        claim = new_claim_id()
        artifact = new_artifact_id()
        self.assertTrue(claim.startswith(CLAIM_ID_PREFIX))
        self.assertTrue(artifact.startswith(ARTIFACT_ID_PREFIX))
        self.assertTrue(valid_claim_id(claim))
        self.assertTrue(valid_artifact_id(artifact))

    def test_an_artifact_id_is_not_a_path(self):
        artifact = new_artifact_id()
        self.assertNotIn("/", artifact)
        self.assertNotIn(".", artifact)
        self.assertNotIn("\\", artifact)

    def test_ids_are_unique_across_many_mints(self):
        minted = {new_artifact_id() for _ in range(2000)}
        self.assertEqual(len(minted), 2000)

    def test_an_id_is_not_derived_from_content(self):
        """Two mints in the same millisecond still differ."""
        first = new_artifact_id(now_ms=1_700_000_000_000)
        second = new_artifact_id(now_ms=1_700_000_000_000)
        self.assertNotEqual(first, second)

    def test_claim_and_artifact_namespaces_do_not_cross(self):
        self.assertFalse(valid_artifact_id(new_claim_id()))
        self.assertFalse(valid_claim_id(new_artifact_id()))


class DigestTests(unittest.TestCase):
    def test_the_digest_is_stable_and_hex(self):
        digest = artifact_digest(b"hello")
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, artifact_digest(b"hello"))
        self.assertTrue(all(c in "0123456789abcdef" for c in digest))

    def test_changed_bytes_change_the_digest(self):
        self.assertNotEqual(artifact_digest(b"hello"), artifact_digest(b"hellp"))

    def test_the_length_prefix_removes_a_concatenation_ambiguity(self):
        self.assertNotEqual(artifact_digest(b"ab"), artifact_digest(b"a") )

    def test_it_refuses_anything_that_is_not_bytes(self):
        with self.assertRaises(TypeError):
            artifact_digest("hello")


class ProvenanceTests(unittest.TestCase):
    """The heart of the milestone."""

    def test_a_change_claim_is_always_adapter_reported(self):
        claim = ChangeClaim(
            claim_id=new_claim_id(),
            task_id="task_x",
            turn_number=1,
            operation=CLAIM_MODIFIED,
            path="a.py",
        )
        self.assertEqual(claim.source, EVIDENCE_ADAPTER_REPORTED)
        self.assertEqual(claim.to_dict()["source"], EVIDENCE_ADAPTER_REPORTED)

    def test_a_change_claim_is_never_verified(self):
        claim = ChangeClaim(
            claim_id=new_claim_id(),
            task_id="task_x",
            turn_number=1,
            operation=CLAIM_MODIFIED,
            path="a.py",
        )
        self.assertFalse(claim.verified)
        self.assertIs(claim.to_dict()["verified"], False)
        self.assertNotIn(claim.source, VERIFIED_EVIDENCE_SOURCES)

    def test_an_adapter_cannot_construct_a_claim_that_claims_to_be_observed(self):
        """Even when the constructor is called directly with a better word."""
        claim = ChangeClaim(
            claim_id=new_claim_id(),
            task_id="task_x",
            turn_number=1,
            operation=CLAIM_MODIFIED,
            path="a.py",
            source=EVIDENCE_GIT_OBSERVED,  # type: ignore[arg-type]
        )
        # The published shape is what downstream reads, and it is a constant.
        self.assertEqual(claim.to_dict()["source"], EVIDENCE_ADAPTER_REPORTED)
        self.assertIs(claim.to_dict()["verified"], False)

    def test_an_artifact_record_is_os_observed(self):
        record = ArtifactRecord(
            artifact_id=new_artifact_id(),
            task_id="task_x",
            claim_id=new_claim_id(),
            path="a.py",
            digest=artifact_digest(b"x"),
            size_bytes=1,
            preview=None,
            preview_truncated=False,
            reason=REASON_OK,
        )
        self.assertEqual(record.source, EVIDENCE_OS_OBSERVED)
        self.assertIn(record.source, VERIFIED_EVIDENCE_SOURCES)

    def test_the_two_records_carry_different_provenance(self):
        """The one-sentence property, asserted directly."""
        self.assertNotEqual(EVIDENCE_ADAPTER_REPORTED, EVIDENCE_OS_OBSERVED)
        self.assertNotIn(EVIDENCE_ADAPTER_REPORTED, VERIFIED_EVIDENCE_SOURCES)
        self.assertIn(EVIDENCE_OS_OBSERVED, VERIFIED_EVIDENCE_SOURCES)


class StructuralBoundaryTests(unittest.TestCase):
    """Properties held by absence rather than by a check."""

    def _imports(self, module):
        import ast
        import pathlib

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        found = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        return found

    def test_the_claim_path_imports_no_process_network_or_provider(self):
        """Asserted on the parsed imports, not on the prose.

        The module's own docstring says it has no subprocess import, and a
        substring search over the file would happily match that sentence — so
        this reads the AST instead.
        """
        from cofferdam.workstation.tasks import claims

        self.assertEqual(
            self._imports(claims)
            & {
                "subprocess", "socket", "shutil", "requests", "httpx", "urllib",
                "openai", "anthropic", "claude_agent_sdk", "asyncio",
            },
            set(),
        )

    def test_the_module_exposes_no_arbitrary_file_read(self):
        from cofferdam.workstation.tasks import claims

        for forbidden in ("read_file", "read_path", "open_path", "cat", "fetch"):
            self.assertFalse(hasattr(claims, forbidden), forbidden)

    @unittest.skipIf(_httpx is None, "workstation extras are not installed")
    def test_no_bridge_artifact_operation_exists(self):
        from cofferdam.actions_bridge.internal import ALLOWED_UPSTREAM_ROUTES
        from cofferdam.actions_bridge.service import OPERATION_IDS

        self.assertEqual(len(OPERATION_IDS), 14)
        for name in OPERATION_IDS:
            self.assertNotIn("rtifact", name)
        for route in ALLOWED_UPSTREAM_ROUTES:
            self.assertNotIn("rtifact", route)
            self.assertNotIn("claim", route)

    def test_the_bridge_still_reports_artifacts_unsupported(self):
        """Read as source, so it holds on the stdlib-only runner too.

        ``normalize`` itself imports nothing outside the standard library, but
        importing it by name would pull the package `__init__`; reading the file
        keeps this assertion available where the bridge extras are absent.
        """
        import pathlib

        source = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1]
            / "cofferdam"
            / "actions_bridge"
            / "normalize.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"artifacts_supported": False', source)
        self.assertIn("no_task_owned_artifact_model", source)


class ResolverHardeningTests(unittest.TestCase):
    """The `O_NONBLOCK` fix in `mind/documents.py`, from the Mind side.

    The regression is a **hang**, not a wrong answer, so each case is bounded by
    the test process finishing at all. The refusal semantics around it must be
    exactly what they were: one coarse code for every way a target can fail to
    be a readable regular file, so a refusal cannot describe the host's
    filesystem one attempt at a time.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-resolver-")
        self.root = Path(self._temp.name) / "vault"
        self.root.mkdir()

    def tearDown(self):
        self._temp.cleanup()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "platform has no FIFO")
    def test_a_fifo_does_not_block_the_resolver(self):
        from cofferdam.workstation.mind.documents import open_target
        from cofferdam.workstation.mind.errors import RoleUnavailable

        os.mkfifo(str(self.root / "pipe.md"))
        with self.assertRaises(RoleUnavailable):
            with open_target(self.root, "pipe.md"):
                pass  # pragma: no cover - reached only if the refusal regresses

    def test_a_regular_file_reads_exactly_as_before(self):
        from cofferdam.workstation.mind.documents import read_document

        (self.root / "doc.md").write_text("unchanged\n", encoding="utf-8")
        self.assertEqual(read_document(self.root, "doc.md"), b"unchanged\n")

    def test_a_regular_files_inspection_is_unchanged(self):
        from cofferdam.workstation.mind.documents import inspect_document

        (self.root / "doc.md").write_text("hello\n", encoding="utf-8")
        state = inspect_document(self.root, "doc.md")
        self.assertEqual(state.size, 6)
        self.assertEqual(len(state.content_hash), 64)

    def test_symlink_directory_and_device_all_refuse_the_same_way(self):
        from cofferdam.workstation.mind.documents import open_target
        from cofferdam.workstation.mind.errors import RoleUnavailable

        outside = Path(self._temp.name) / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        os.symlink(outside, self.root / "link.md")
        (self.root / "adir").mkdir()

        messages = set()
        cases = ["link.md", "adir", "missing.md"]
        if Path("/dev/null").exists():
            os.symlink("/dev/null", self.root / "dev.md")
            cases.append("dev.md")
        for name in cases:
            with self.assertRaises(RoleUnavailable) as caught:
                with open_target(self.root, name):
                    pass  # pragma: no cover
            messages.add(str(caught.exception))

        # The refusal must not tell the caller which kind of thing it was.
        for message in messages:
            for leak in ("fifo", "pipe", "socket", "device", "symlink", "link"):
                self.assertNotIn(leak, message.lower(), message)
            self.assertNotIn(str(self.root), message)

    def test_the_nonblock_flag_is_actually_set(self):
        """Asserted on source, so a later edit that drops it fails here."""
        import pathlib

        from cofferdam.workstation.mind import documents

        source = pathlib.Path(documents.__file__).read_text(encoding="utf-8")
        self.assertIn("_O_NONBLOCK", source)
        self.assertIn("os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK", source)


class ObservationTests(unittest.TestCase):
    """Record-time observation against a real synthetic project root."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="m2k-obs-")
        self.root = Path(self._temp.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self._temp.cleanup()

    def write(self, relative, data):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            target.write_text(data, encoding="utf-8")
        else:
            target.write_bytes(data)
        return target

    def test_a_real_text_file_is_digested_sized_and_previewed(self):
        self.write("src/foo.py", "print('hi')\n")
        observed = observe_artifact(self.root, "src/foo.py")
        self.assertEqual(observed.reason, REASON_OK)
        self.assertEqual(observed.size_bytes, len(b"print('hi')\n"))
        self.assertEqual(observed.digest, artifact_digest(b"print('hi')\n"))
        self.assertEqual(observed.preview, "print('hi')\n")
        self.assertFalse(observed.preview_truncated)

    def test_a_missing_file_is_a_reason_not_a_failure(self):
        observed = observe_artifact(self.root, "src/never.py")
        self.assertEqual(observed.reason, REASON_ARTIFACT_MISSING)
        self.assertIsNone(observed.digest)
        self.assertIsNone(observed.size_bytes)

    def test_a_deleted_file_claim_is_recordable_without_the_file(self):
        """A delete claim must not require the file to still exist."""
        observed = observe_artifact(self.root, "src/gone.py")
        self.assertEqual(observed.reason, REASON_ARTIFACT_MISSING)
        self.assertFalse(observed.captured)

    def test_a_directory_is_not_an_artifact(self):
        (self.root / "pkg").mkdir()
        observed = observe_artifact(self.root, "pkg")
        self.assertIn(
            observed.reason, (REASON_ARTIFACT_NOT_REGULAR, REASON_ARTIFACT_MISSING)
        )
        self.assertIsNone(observed.digest)

    def test_a_symlink_target_is_refused_rather_than_followed(self):
        outside = Path(self._temp.name) / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        os.symlink(outside, self.root / "link.txt")
        observed = observe_artifact(self.root, "link.txt")
        self.assertEqual(observed.reason, REASON_ARTIFACT_MISSING)
        self.assertIsNone(observed.digest)

    def test_a_symlinked_directory_component_is_refused(self):
        outside = Path(self._temp.name) / "outside"
        outside.mkdir()
        (outside / "x.txt").write_text("secret\n", encoding="utf-8")
        os.symlink(outside, self.root / "linkdir")
        observed = observe_artifact(self.root, "linkdir/x.txt")
        self.assertEqual(observed.reason, REASON_ARTIFACT_MISSING)
        self.assertIsNone(observed.digest)

    def test_a_denied_path_is_never_opened(self):
        self.write(".env", "SECRET_TOKEN=abcdefghijklmnop\n")
        observed = observe_artifact(self.root, ".env")
        self.assertEqual(observed.reason, REASON_PATH_DENIED_SENSITIVE)
        self.assertIsNone(observed.digest)
        self.assertIsNone(observed.size_bytes)
        self.assertIsNone(observed.preview)

    def test_a_denied_path_holding_real_looking_content_stores_nothing(self):
        self.write("certs/server.pem", "-----BEGIN PRIVATE KEY-----\nZZZZ\n")
        observed = observe_artifact(self.root, "certs/server.pem")
        self.assertEqual(observed.reason, REASON_PATH_DENIED_SENSITIVE)
        self.assertIsNone(observed.preview)

    def test_an_oversized_file_gets_no_digest_rather_than_a_prefix_digest(self):
        self.write("big.py", "x" * (MAX_ARTIFACT_READ_BYTES + 10))
        observed = observe_artifact(self.root, "big.py")
        self.assertEqual(observed.reason, REASON_ARTIFACT_TOO_LARGE)
        self.assertIsNone(observed.digest)
        self.assertIsNone(observed.size_bytes)

    def test_a_binary_file_is_not_decoded_with_replacement(self):
        self.write("data.py", b"\xff\xfe\x00\x01binary")
        observed = observe_artifact(self.root, "data.py")
        self.assertIsNotNone(observed.digest)  # the bytes are still a fact
        self.assertIsNone(observed.preview)
        self.assertEqual(observed.reason, REASON_PREVIEW_UNSUPPORTED_TYPE)

    def test_an_unsupported_type_keeps_metadata_and_omits_the_preview(self):
        self.write("image.bin", b"\x89PNG\r\n\x1a\n")
        observed = observe_artifact(self.root, "image.bin")
        self.assertIsNotNone(observed.digest)
        self.assertIsNotNone(observed.size_bytes)
        self.assertIsNone(observed.preview)
        self.assertEqual(observed.reason, REASON_PREVIEW_UNSUPPORTED_TYPE)

    def test_a_long_text_file_previews_bounded(self):
        self.write("long.md", "a" * (MAX_PREVIEW_BYTES * 3))
        observed = observe_artifact(self.root, "long.md")
        self.assertIsNotNone(observed.preview)
        self.assertLessEqual(len(observed.preview.encode("utf-8")), MAX_PREVIEW_BYTES)
        self.assertTrue(observed.preview_truncated)

    def test_a_multibyte_boundary_does_not_produce_a_partial_character(self):
        self.write("uni.md", "é" * (MAX_PREVIEW_BYTES))
        observed = observe_artifact(self.root, "uni.md")
        self.assertIsNotNone(observed.preview)
        self.assertLessEqual(len(observed.preview.encode("utf-8")), MAX_PREVIEW_BYTES)
        observed.preview.encode("utf-8").decode("utf-8")  # must not raise

    def test_the_whole_file_is_never_copied_when_it_exceeds_the_preview(self):
        body = "z" * (MAX_PREVIEW_BYTES * 2)
        self.write("big.md", body)
        observed = observe_artifact(self.root, "big.md")
        self.assertLess(len(observed.preview), len(body))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "platform has no FIFO")
    def test_a_fifo_is_refused_without_blocking(self):
        """The regression this test exists for is a hang, not a wrong answer.

        Opening a FIFO ``O_RDONLY`` blocks until a writer appears. Before the
        ``O_NONBLOCK`` fix in ``mind/documents.py`` this call never returned, so
        a claim naming a named pipe would stall the recording path indefinitely.

        The *reason code* is deliberately the coarse one: ``open_target``
        collapses missing, link, directory and device into a single refusal so
        that a refusal cannot describe the host's filesystem one attempt at a
        time. Asserting the precise kind here would be asking that doctrine to
        be weakened.
        """
        os.mkfifo(str(self.root / "pipe.txt"))
        observed = observe_artifact(self.root, "pipe.txt")
        self.assertIn(
            observed.reason, (REASON_ARTIFACT_MISSING, REASON_ARTIFACT_NOT_REGULAR)
        )
        self.assertIsNone(observed.digest)
        self.assertIsNone(observed.preview)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "platform has no FIFO")
    def test_a_fifo_claim_records_without_hanging_end_to_end(self):
        os.mkfifo(str(self.root / "pipe2.txt"))
        observed = observe_artifact(self.root, "pipe2.txt")
        self.assertFalse(observed.captured)

    def test_an_escape_never_reaches_the_filesystem_walk(self):
        outside = Path(self._temp.name) / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        observed = observe_artifact(self.root, "../outside.txt")
        self.assertIsNone(observed.digest)
        self.assertNotEqual(observed.reason, REASON_OK)

    def test_a_missing_project_root_is_a_reason_not_a_crash(self):
        observed = observe_artifact(Path(self._temp.name) / "nope", "a.py")
        self.assertIsNone(observed.digest)
        self.assertNotEqual(observed.reason, REASON_OK)

    def test_content_change_changes_the_recorded_digest(self):
        self.write("src/foo.py", "one\n")
        first = observe_artifact(self.root, "src/foo.py")
        self.write("src/foo.py", "two\n")
        second = observe_artifact(self.root, "src/foo.py")
        self.assertNotEqual(first.digest, second.digest)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
