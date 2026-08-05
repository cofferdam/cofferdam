"""Registries are overlays and definitions — never runtime discovery.

The M2A registries were first written the wrong way round: they shipped named
displays and personal browser labels, which read as though Cofferdam had already
looked at the machine and found them. It had not, and it still does not — there
is no runtime discovery in this build at all.

`DECISIONS.md` D-2026-08-04-6 splits the world into three layers:

* **definitions** — code-owned, not configurable (which applications exist as a
  concept, which launch adapters are permitted);
* **runtime resources** — what is actually here right now: connected displays,
  running processes, application instances, windows. **Not implemented yet**;
* **user overlays** — optional labels, aliases, preferences, policy metadata.
  This is all a registry file is.

These tests pin the consequences of that split, because every one of them is a
property a future change could quietly break while every other test stayed
green. They are standard-library only, so they run on the stdlib-only CI path
too.
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.registries.loader import REGISTRY_NAMES, load_registries

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples" / "registries"
WEB_DIR = REPO_ROOT / "web"
PACKAGE_ROOT = REPO_ROOT / "cofferdam"

# Labels that describe somebody's actual desk. Shipping any of these would be
# claiming a discovery that never happened.
PRE_NAMED_RESOURCES = (
    "large-monitor",
    "small-monitor",
    "laptop-panel",
    "laptop-monitor",
    "main-monitor",
    "personal-opera",
    "fallback-firefox",
    "main-browser",
    "backup-browser",
    "büyük monitör",
    "küçük monitör",
    "kişisel opera",
    "yedek firefox",
    "yedek mozilla",
    "ana monitör",
    "ana tarayıcı",
    "kişisel tarayıcı",
    "yedek tarayıcı",
)


def _python_sources():
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _example_documents():
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


class ExamplesAreNotConfigurationTests(unittest.TestCase):
    """Committed examples illustrate a file format. Nothing installs them."""

    def test_no_code_path_reads_or_copies_the_examples_directory(self) -> None:
        """The defect this prevents: a "helpful" first-run seeding step.

        Copying the examples in would populate a real machine's configuration —
        and the PWA — with devices and displays that do not exist.
        """
        offenders = []
        for path in _python_sources():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                if "examples/registries" in node.value or "examples" == node.value:
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "no runtime code may reference the committed examples directory; "
            "registries are user-authored and are never seeded: " + ", ".join(offenders),
        )

    def test_no_shipped_script_copies_examples_into_cofferdam_home(self) -> None:
        for path in sorted((REPO_ROOT / "deploy").glob("*")):
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            stripped = "\n".join(
                line for line in body.splitlines() if not line.strip().startswith("#")
            )
            with self.subTest(script=path.name):
                self.assertNotIn(
                    "examples/registries",
                    stripped,
                    f"{path.name} appears to install example registries onto a real machine",
                )

    def test_every_overlay_example_id_and_name_is_marked_as_an_example(self) -> None:
        """An id that looks real gets treated as real by whoever reads it.

        ``applications.json`` is exempt, and the exemption is the point of the
        three-layer split: it mirrors **definitions**, which are code-owned. Its
        ids are neutral concept names (``opera``, ``firefox``) that the source
        already defines, so naming one ``example-opera`` would be the lie here.
        Every other registry is a **user overlay**, where a realistic-looking
        entry implies a discovery that never happened.
        """
        for path, document in _example_documents():
            if path.name == "applications.json":
                continue
            for item in document.get("items", []):
                with self.subTest(file=path.name, item=item.get("id")):
                    self.assertTrue(
                        item["id"].startswith("example"),
                        f"{path.name}: id {item['id']!r} must begin with 'example'",
                    )
                    self.assertIn(
                        "example",
                        item["name"].lower(),
                        f"{path.name}: name {item['name']!r} must say it is an example",
                    )

    def test_application_definitions_keep_neutral_concept_ids(self) -> None:
        """The other half of the rule: definitions must NOT be example-prefixed."""
        applications = json.loads((EXAMPLES_DIR / "applications.json").read_text(encoding="utf-8"))
        for item in applications["items"]:
            with self.subTest(application=item["id"]):
                self.assertFalse(
                    item["id"].startswith("example"),
                    "an application definition names a real code-owned concept; prefixing it "
                    "'example-' would make the definition layer look optional",
                )

    def test_examples_ship_no_pre_named_real_world_resource(self) -> None:
        """No `large-monitor`, no `personal-opera`, in any committed example."""
        for path, _ in _example_documents():
            body = path.read_text(encoding="utf-8").lower()
            for banned in PRE_NAMED_RESOURCES:
                with self.subTest(file=path.name, banned=banned):
                    self.assertNotIn(
                        banned,
                        body,
                        f"{path.name} ships a pre-named resource ({banned!r}). A resource is "
                        "discovered first and labelled afterwards.",
                    )

    def test_examples_never_point_a_profile_at_an_undiscovered_display(self) -> None:
        """A preferred display is a wish, and must not imply one was found."""
        profiles = json.loads((EXAMPLES_DIR / "browser_profiles.json").read_text(encoding="utf-8"))
        for item in profiles["items"]:
            with self.subTest(profile=item["id"]):
                self.assertIsNone(
                    item.get("preferred_display_id"),
                    "a committed example must not reference a display id, because no display "
                    "has been discovered for it to mean",
                )


class EmptyConfigurationIsHonestTests(unittest.TestCase):
    """A machine with no registry files is fully working, not broken."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.config = load_config(home=self.home)
        self.config.ensure_dirs()

    def test_loading_with_no_files_yields_empty_registries_and_no_error(self) -> None:
        loaded = load_registries(self.config)
        for name in REGISTRY_NAMES:
            with self.subTest(registry=name):
                load = loaded.load(name)
                self.assertIsNone(load.error)
                self.assertEqual(list(load.registry.items), [])

    def test_loading_never_creates_a_registry_file(self) -> None:
        """Reading must not write. A created file is a claim the user did not make."""
        load_registries(self.config)
        created = sorted(p.name for p in (self.home / "config" / "registries").glob("*.json"))
        self.assertEqual(
            created,
            [],
            "loading registries must not materialise any file; an absent registry stays absent",
        )

    def test_loading_twice_is_still_empty(self) -> None:
        load_registries(self.config)
        loaded = load_registries(self.config)
        self.assertEqual(sum(len(loaded.load(n).registry.items) for n in REGISTRY_NAMES), 0)


class LayerSeparationTests(unittest.TestCase):
    """Definitions, runtime resources, and overlays must not be conflated."""

    def test_a_registry_cannot_introduce_an_application_definition(self) -> None:
        """Definitions are code-owned: configuration selects, it never adds."""
        from cofferdam.workstation.adapters.base import APPLICATION_KEYS

        applications = json.loads(
            (EXAMPLES_DIR / "browser_profiles.json").read_text(encoding="utf-8")
        )
        for item in applications["items"]:
            with self.subTest(profile=item["id"]):
                self.assertIn(
                    item["application_id"],
                    tuple(APPLICATION_KEYS) + ("opera",),
                    "a browser profile may only reference an application the code already "
                    "defines; a registry can never introduce one",
                )

    def test_no_registry_schema_can_hold_an_executable_or_command(self) -> None:
        """The hard boundary: no path, argv, or shell fragment is representable."""
        forbidden = ("exec", "command", "argv", "shell", "binary", "path", "cmd", "env")
        for path, document in _example_documents():
            for item in document.get("items", []):
                for field in item:
                    with self.subTest(file=path.name, field=field):
                        self.assertNotIn(
                            field.lower(),
                            forbidden,
                            f"{path.name}: field {field!r} would let configuration name an "
                            "executable or command",
                        )

    def test_a_browser_profile_is_not_evidence_of_a_running_browser(self) -> None:
        """A profile carries no field that could assert liveness."""
        profiles = json.loads((EXAMPLES_DIR / "browser_profiles.json").read_text(encoding="utf-8"))
        runtime_fields = ("pid", "window_id", "tab_id", "running", "open", "instance", "started_at")
        for item in profiles["items"]:
            for field in item:
                with self.subTest(profile=item["id"], field=field):
                    self.assertNotIn(
                        field.lower(),
                        runtime_fields,
                        "a browser profile is a launch preference, not a live instance",
                    )

    def test_display_entries_carry_only_hints_and_labels(self) -> None:
        """A display entry may hint at how to match, never assert a connection."""
        displays = json.loads((EXAMPLES_DIR / "displays.json").read_text(encoding="utf-8"))
        for item in displays["items"]:
            match = item.get("match", {})
            with self.subTest(display=item["id"]):
                self.assertIn("connector_hint", match)
                self.assertNotIn("connected", item)
                self.assertNotIn("resolution", item)
                self.assertNotIn("active", item)

    def test_labels_and_aliases_are_optional(self) -> None:
        """An overlay that is mandatory is not an overlay."""
        for path, document in _example_documents():
            for item in document.get("items", []):
                if "aliases" not in item:
                    continue
                with self.subTest(file=path.name, item=item["id"]):
                    self.assertIsInstance(item["aliases"], list)
        # An entry that carries no alias at all still loads. The schema requires
        # the *field* to be present and explicit — silence about aliases would
        # be ambiguous — but an empty list is a complete answer, so nobody is
        # ever pushed into inventing a label to satisfy the format.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = load_config(home=home)
            config.ensure_dirs()
            config.registry_path("devices").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "id": "unlabelled-device",
                                "name": "Unlabelled",
                                "aliases": [],
                                "enabled": True,
                                "kind": "workstation",
                                "platform": "linux",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            load = load_registries(config).load("devices")
            self.assertIsNone(load.error)
            self.assertEqual(len(load.registry.items), 1)
            self.assertEqual(list(load.registry.items)[0].aliases, ())


class UserInterfaceHonestyTests(unittest.TestCase):
    """The PWA must not describe configuration as machine state."""

    def setUp(self) -> None:
        self.app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    def test_the_registry_panel_denies_being_runtime_state(self) -> None:
        self.assertIn("not a list of what is currently connected", self.index_html.lower())

    def test_the_registry_panel_says_empty_is_normal(self) -> None:
        self.assertIn("empty is normal", self.index_html.lower())

    def test_the_empty_state_does_not_read_as_a_missing_feature(self) -> None:
        self.assertIn("this is normal, and everything still works", self.app_js)

    def test_display_and_browser_sections_are_labelled_as_configuration(self) -> None:
        self.assertIn("Display labels", self.app_js)
        self.assertIn("Browser launch preferences", self.app_js)
        self.assertIn("Application definitions", self.app_js)

    def test_availability_badge_does_not_read_as_running(self) -> None:
        """Raised during live validation: "available" was taken as "running".

        The badge is a fact about the *definition* — the executable was found,
        so a launch would work. Whether an instance is running is runtime
        inventory, which does not exist yet, so the badge must not imply it.
        """
        self.assertIn("installed — can launch", self.app_js)
        self.assertNotIn('badge("available"', self.app_js)
        self.assertNotIn('badge("running"', self.app_js)

    def test_the_ui_never_pre_names_a_resource(self) -> None:
        combined = (self.app_js + self.index_html).lower()
        for banned in PRE_NAMED_RESOURCES:
            with self.subTest(banned=banned):
                self.assertNotIn(banned, combined)


class NoPixelAutomationTests(unittest.TestCase):
    """D-2026-08-04-7: semantic interfaces only, at every layer."""

    # Real coordinate-automation entry points. Matched as calls, so prose and
    # identifiers that merely contain the words do not trip the guard.
    BANNED_CALLS = (
        "pyautogui",
        "pynput",
        "xdotool",
        "ydotool",
        "wtype",
        "xte",
        "pytesseract",
        "tesseract",
    )

    def test_no_coordinate_automation_dependency_is_imported(self) -> None:
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0].lower()
                    with self.subTest(path=path.name, module=name):
                        self.assertNotIn(
                            root,
                            self.BANNED_CALLS,
                            f"{path.name} imports {name!r}; core behaviour must use semantic "
                            "interfaces, never synthetic input or OCR",
                        )

    def test_no_shipped_argv_invokes_a_coordinate_or_ocr_tool(self) -> None:
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                with self.subTest(path=path.name, line=node.lineno):
                    self.assertNotIn(
                        node.value.strip().lower(),
                        self.BANNED_CALLS,
                        f"{path.name}:{node.lineno} runs a coordinate/OCR tool as a command",
                    )

    def test_no_declared_dependency_is_a_coordinate_automation_library(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # Only dependency declarations, not prose in a description field.
        requirements = re.findall(r'"([A-Za-z0-9_.\-]+)\s*[><=!~]*[^"]*"', pyproject)
        for requirement in requirements:
            with self.subTest(requirement=requirement):
                self.assertNotIn(requirement.lower(), self.BANNED_CALLS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
