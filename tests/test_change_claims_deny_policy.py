"""M2K PR1 — the record-time sensitive-path deny policy, and its edges.

D-2026-08-09-3 puts this check at **record time** so unsafe artifact content
never enters the store. These tests are the policy's specification: what it
denies, what it deliberately does not, and the rename rule that keeps a
sensitive *destination* from becoming a way in.

Synthetic paths only. Nothing here names a real host location, and every
"secret" written to disk is a fake marker.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.tasks.claims import (
    CLAIM_MODIFIED,
    CLAIM_RENAMED,
    REASON_OK,
    REASON_PATH_DENIED_SENSITIVE,
    ClaimSubmission,
    is_denied_path,
)
from cofferdam.workstation.tasks.store import TaskStore

FAKE_SECRET = "ZZDENYPOLICYSECRETZZ-not-a-real-credential"


class DeniedConventionTests(unittest.TestCase):
    """Each rule, named, with the convention it encodes."""

    def assertDenied(self, *paths):
        for path in paths:
            self.assertTrue(is_denied_path(path), path)

    def test_docker_credential_config(self):
        """`~/.docker/config.json` holds registry auth. The subtree goes."""
        self.assertDenied(".docker/config.json", ".docker/anything", "a/.docker/config.json")

    def test_kubernetes_credential_tree(self):
        """kubeconfig carries cluster certs and tokens; the cache carries tokens too."""
        self.assertDenied(".kube/config", ".kube/cache/x", "deep/.kube/config")

    def test_terraform_state(self):
        """State files routinely contain plaintext provider and database secrets."""
        self.assertDenied(
            "terraform.tfstate", "terraform.tfstate.backup", "infra/prod.tfstate"
        )

    def test_vault_token(self):
        self.assertDenied(".vault-token", "home/.vault-token")

    def test_pkcs8_private_keys(self):
        """`.p8` is a private-key container; Apple's `AuthKey_*.p8` is one case of it."""
        self.assertDenied("AuthKey.p8", "AuthKey_ABC123.p8", "keys/service.p8")

    def test_environment_file_variants(self):
        self.assertDenied(
            ".env",
            ".env.local",
            ".env-local",
            ".env_production",
            "local.env",
            "prod.env",
            "svc/.env",
        )

    def test_credential_file_backups(self):
        """One stripping rule, not one rule per backup extension."""
        self.assertDenied(
            ".netrc.bak",
            ".pgpass.old",
            "certs/private.pem.bak",
            "id_rsa.orig",
            ".env.save",
            "terraform.tfstate.backup",
        )

    def test_application_credential_configs(self):
        self.assertDenied(
            "config/database.yml", "config/secrets.yml", "config/secrets.yaml"
        )

    def test_obvious_secret_basenames(self):
        self.assertDenied("secret.txt", "secrets.txt", "a/b/secret.txt")

    def test_the_pre_existing_rules_still_hold(self):
        self.assertDenied(
            ".env", ".netrc", ".pgpass", ".htpasswd", "id_rsa", "keys/id_ed25519",
            "credentials", "app/credentials.json", "secrets.json",
            "service-account.json", "token", ".git-credentials", ".npmrc", ".pypirc",
            "certs/x.pem", "a.key", "x.p12", "y.pfx", "z.jks", "w.keystore",
            ".ssh/config", ".gnupg/x", ".aws/credentials", ".cofferdam/state",
            "secrets/app.md",
        )

    def test_matching_is_case_insensitive(self):
        self.assertDenied(
            ".DOCKER/CONFIG.JSON", ".Kube/Config", "TERRAFORM.TFSTATE",
            ".VAULT-TOKEN", "AUTHKEY.P8", "LOCAL.ENV", ".NETRC.BAK",
            "CONFIG/DATABASE.YML", "SECRET.TXT",
        )


class SafeNearMissTests(unittest.TestCase):
    """The false-positive boundary, asserted rather than hoped for."""

    def assertAllowed(self, *paths):
        for path in paths:
            self.assertFalse(is_denied_path(path), path)

    def test_documentation_about_secrets_is_not_a_secret(self):
        self.assertAllowed(
            "docs/environment.md",
            "docs/secrets-design.md",
            "docs/SECRETS.md",
            "docs/env.md",
            "README.md",
        )

    def test_example_and_template_credential_files_stay_allowed(self):
        self.assertAllowed(
            "config/database.example.yml",
            "config/database.yml.example",
            "config/database.sample.yml",
        )

    def test_dot_env_example_stays_denied_as_it_already_was(self):
        """Not a false positive to fix — the pre-existing `.env.` rule catches it.

        A committed `.env.example` holds placeholders rather than credentials, so
        denying it costs a preview nobody needs. Relaxing a rule that already
        ships to admit it would be widening the policy in the name of tidiness.
        """
        self.assertTrue(is_denied_path(".env.example"))

    def test_code_whose_name_contains_a_keyword_stays_allowed(self):
        self.assertAllowed(
            "src/tokenizer.py",
            "src/token_utils.py",
            "lib/credentials_test.py",
            "a/keychain.md",
            "src/environment.py",
            "tests/test_secrets_policy.py",
        )

    def test_dot_environment_is_not_a_dot_env_variant(self):
        """`.env` + a separator is the convention; `.environment` is a word."""
        self.assertAllowed(".environment", ".envoy", ".envrc.md")

    def test_backup_stripping_does_not_deny_ordinary_backups(self):
        self.assertAllowed(
            "notes.md.bak", "README.md.old", "src/main.py.orig", "a/b.txt.save"
        )

    def test_near_miss_extensions_stay_allowed(self):
        self.assertAllowed(
            "my.p8x", "state.tfstate.md", "public.crt", "id_rsa.pub", "docker/Dockerfile"
        )

    def test_a_directory_named_like_a_tool_without_the_dot_stays_allowed(self):
        self.assertAllowed("docker/config.json", "kube/config", "docs/docker/notes.md")


class DenyAtRecordTimeTests(unittest.TestCase):
    """Through the store, on the paths the review named."""

    def setUp(self):
        from cofferdam.workstation.config import load_config

        self._temp = tempfile.TemporaryDirectory(prefix="m2k-deny-")
        self.home = Path(self._temp.name)
        self.root = self.home / "project"
        self.root.mkdir()
        config = load_config(self.home)
        config.ensure_dirs()
        self.store = TaskStore(config)
        row, _ = self.store.create_task(
            origin="pwa", adapter_id="validation", project_id="synth",
            prompt="p", title="t",
        )
        self.task_id = row.task_id

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self._temp.cleanup()

    def write(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def record(self, *submissions):
        return self.store.record_change_claims(
            self.task_id, list(submissions), project_root=self.root, turn_number=1
        )

    def db_bytes(self):
        self.store.close()
        return (self.home / "state" / "tasks" / "tasks.sqlite3").read_bytes()

    #: Every path the review named, with plausible credential content.
    CASES = {
        ".docker/config.json": '{"auths":{"r.io":{"auth":"%s"}}}',
        ".kube/config": "users:\n- user:\n    token: %s\n",
        "terraform.tfstate": '{"outputs":{"db":{"value":"%s"}}}',
        ".vault-token": "%s",
        "AuthKey_ABC123.p8": "-----BEGIN PRIVATE KEY-----\n%s\n",
        ".env-local": "API_KEY=%s\n",
        "local.env": "API_KEY=%s\n",
        ".netrc.bak": "machine h login u password %s\n",
        ".pgpass.old": "h:5432:db:user:%s\n",
        "certs/private.pem.bak": "-----BEGIN PRIVATE KEY-----\n%s\n",
        "config/database.yml": "production:\n  password: %s\n",
        "secret.txt": "%s\n",
    }

    def test_every_named_path_stores_no_bytes_and_no_preview(self):
        for relative, template in self.CASES.items():
            self.write(relative, template % FAKE_SECRET)
        claims, artifacts, ingestion = self.record(
            *[
                ClaimSubmission(operation=CLAIM_MODIFIED, path=relative)
                for relative in self.CASES
            ]
        )
        self.assertEqual(len(claims), len(self.CASES))
        for record in artifacts:
            self.assertEqual(
                record.reason, REASON_PATH_DENIED_SENSITIVE, record.path
            )
            self.assertIsNone(record.digest, record.path)
            self.assertIsNone(record.size_bytes, record.path)
            self.assertIsNone(record.preview, record.path)
        self.assertEqual(
            ingestion.reason_counts.get(REASON_PATH_DENIED_SENSITIVE),
            len(self.CASES),
        )

    def test_none_of_the_denied_content_reaches_the_database_bytes(self):
        for relative, template in self.CASES.items():
            self.write(relative, template % FAKE_SECRET)
        self.record(
            *[
                ClaimSubmission(operation=CLAIM_MODIFIED, path=relative)
                for relative in self.CASES
            ]
        )
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_the_claim_row_is_kept_so_provenance_is_not_lost(self):
        self.write(".docker/config.json", '{"auths":{"x":{"auth":"%s"}}}' % FAKE_SECRET)
        claims, _, ingestion = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path=".docker/config.json")
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].reason, REASON_PATH_DENIED_SENSITIVE)
        # A withheld artifact is not a rejected claim.
        self.assertEqual(ingestion.rejected, 0)
        self.assertEqual(ingestion.accepted, 1)

    def test_a_safe_neighbour_in_the_same_batch_is_still_observed(self):
        self.write(".env-local", "K=%s\n" % FAKE_SECRET)
        self.write("docs/environment.md", "ordinary prose\n")
        _, artifacts, _ = self.record(
            ClaimSubmission(operation=CLAIM_MODIFIED, path=".env-local"),
            ClaimSubmission(operation=CLAIM_MODIFIED, path="docs/environment.md"),
        )
        by_path = {a.path: a for a in artifacts}
        self.assertIsNone(by_path[".env-local"].digest)
        self.assertIsNotNone(by_path["docs/environment.md"].digest)
        self.assertEqual(by_path["docs/environment.md"].preview, "ordinary prose\n")


class RenameDenyTests(DenyAtRecordTimeTests):
    """A sensitive destination must not become a way in."""

    def test_a_safe_source_renamed_to_a_sensitive_destination_stores_nothing(self):
        self.write("safe.txt", "SOURCE-%s\n" % FAKE_SECRET)
        claims, artifacts, ingestion = self.record(
            ClaimSubmission(operation=CLAIM_RENAMED, path="safe.txt", to_path=".env")
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].reason, REASON_PATH_DENIED_SENSITIVE)
        self.assertIsNone(artifacts[0].digest)
        self.assertIsNone(artifacts[0].preview)
        self.assertEqual(ingestion.rejected, 0)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_the_docker_destination_case_from_the_review(self):
        self.write("config.json", "PAYLOAD-%s\n" % FAKE_SECRET)
        _, artifacts, _ = self.record(
            ClaimSubmission(
                operation=CLAIM_RENAMED,
                path="config.json",
                to_path=".docker/config.json",
            )
        )
        self.assertIsNone(artifacts[0].digest)
        self.assertIsNone(artifacts[0].preview)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_a_sensitive_source_renamed_to_a_safe_destination_stores_nothing(self):
        self.write(".env", "K=%s\n" % FAKE_SECRET)
        _, artifacts, _ = self.record(
            ClaimSubmission(operation=CLAIM_RENAMED, path=".env", to_path="safe.md")
        )
        self.assertEqual(artifacts[0].reason, REASON_PATH_DENIED_SENSITIVE)
        self.assertIsNone(artifacts[0].digest)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_a_rename_sensitive_on_both_sides_stores_nothing(self):
        self.write(".env", "K=%s\n" % FAKE_SECRET)
        _, artifacts, _ = self.record(
            ClaimSubmission(operation=CLAIM_RENAMED, path=".env", to_path=".env.local")
        )
        self.assertIsNone(artifacts[0].digest)
        self.assertNotIn(FAKE_SECRET.encode(), self.db_bytes())

    def test_a_rename_safe_on_both_sides_is_observed_normally(self):
        self.write("a.md", "ordinary\n")
        _, artifacts, _ = self.record(
            ClaimSubmission(operation=CLAIM_RENAMED, path="a.md", to_path="b.md")
        )
        self.assertEqual(artifacts[0].reason, REASON_OK)
        self.assertIsNotNone(artifacts[0].digest)

    def test_the_ingestion_summary_holds_no_sensitive_path(self):
        self.write("safe.txt", "x\n")
        self.record(
            ClaimSubmission(operation=CLAIM_RENAMED, path="safe.txt", to_path=".env")
        )
        self.store.close()
        with sqlite3.connect(str(self.home / "state/tasks/tasks.sqlite3")) as db:
            raw = db.execute(
                "SELECT reason_counts_json FROM task_claim_ingestion"
            ).fetchone()[0]
        self.assertNotIn(".env", raw)
        self.assertIn(REASON_PATH_DENIED_SENSITIVE, raw)


class PolicyAuthorityTests(unittest.TestCase):
    def test_no_adapter_or_caller_field_can_override_the_policy(self):
        for field in ("allow", "force", "override", "deny", "policy", "sensitive"):
            self.assertNotIn(field, set(ClaimSubmission.__dataclass_fields__))

    def test_the_policy_takes_no_configuration_argument(self):
        import inspect

        signature = inspect.signature(is_denied_path)
        self.assertEqual(list(signature.parameters), ["relative"])

    def test_no_bridge_or_api_surface_was_added(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "cofferdam"
            / "actions_bridge"
            / "normalize.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"artifacts_supported": False', source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
