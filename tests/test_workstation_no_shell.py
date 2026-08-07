"""Structural safety checks over the workstation source itself.

Covers required check 12 (no committed secrets) and reinforces check 6: the
"no arbitrary shell" property is enforced by **construction**, so it is
asserted against the source tree rather than only against request handling.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "cofferdam" / "workstation"
WEB_ROOT = REPO_ROOT / "web"

# Field names that would turn a typed action back into a command channel.
FORBIDDEN_FIELDS = {
    "command",
    "cmd",
    "args",
    "argv",
    "shell",
    "exec",
    "executable",
    "path",
    "script",
}


def _python_sources():
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _forbids_extra(node: ast.ClassDef) -> bool:
    """True if the class body assigns ``model_config = ConfigDict(extra="forbid")``."""
    for statement in node.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "model_config" for t in statement.targets):
            continue
        call = statement.value
        if isinstance(call, ast.Call):
            for keyword in call.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Constant):
                    return keyword.value.value == "forbid"
    return False


def _annotated_fields(node: ast.ClassDef) -> set:
    """Annotated attribute names declared directly in the class body."""
    return {
        statement.target.id
        for statement in node.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }


def _param_schema_names(tree: ast.Module) -> set:
    """Schema class names referenced as values of the PARAM_SCHEMAS mapping."""
    for statement in ast.walk(tree):
        target = None
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target = statement.target.id
        elif isinstance(statement, ast.Assign):
            names = [t.id for t in statement.targets if isinstance(t, ast.Name)]
            target = names[0] if names else None
        if target != "PARAM_SCHEMAS" or not isinstance(statement.value, ast.Dict):
            continue
        return {v.id for v in statement.value.values if isinstance(v, ast.Name)}
    return set()


class NoShellExecutionTests(unittest.TestCase):
    def test_no_module_uses_a_shell(self) -> None:
        """No ``shell=True``, ``os.system``, or ``os.popen`` anywhere."""
        offenders = []
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and not (
                            isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                        ):
                            offenders.append(f"{path.name}:{node.lineno} shell= argument")
                    target = node.func
                    if isinstance(target, ast.Attribute) and target.attr in ("system", "popen"):
                        offenders.append(f"{path.name}:{node.lineno} os.{target.attr}")
        self.assertEqual(offenders, [], f"shell execution found: {offenders}")

    def test_subprocess_is_only_called_from_the_adapter_helpers(self) -> None:
        """Subprocess use is centralized in adapter code, and nowhere else.

        Two locations, both of them adapters, both of them named here so that a
        third one is a failing test rather than a review someone skims.

        ``adapters/base.py`` runs the desktop's own tools. The Claude Code task
        adapter runs the Claude Code CLI and its Git observations. The property
        being protected is not "one file" — it is that a request handler, a
        model, the store, or the service layer can never reach a process, and
        that is still exactly true.

        The companion guard above still applies to every one of these files:
        ``shell=`` must be ``False``, and ``os.system``/``os.popen`` are absent
        from the whole package.
        """
        offenders = []
        for path in _python_sources():
            if path.name == "base.py" and path.parent.name == "adapters":
                continue
            if "claude_code" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "subprocess." in source:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"subprocess used outside adapter code: {offenders}")

    def test_action_schemas_expose_no_command_like_field(self) -> None:
        """The action schemas declare no command-like field, and forbid extras.

        Checked by parsing the source rather than importing it: this module is a
        structural scan, and importing ``actions`` would drag in pydantic, which
        the Trust Core stays free of. The equivalent runtime assertion against
        the live pydantic models lives in ``test_workstation_actions.py``.
        """
        tree = ast.parse((PACKAGE_ROOT / "actions.py").read_text(encoding="utf-8"), filename="actions.py")
        classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

        # The shared base must forbid unknown fields; every schema inherits it.
        self.assertIn("_Params", classes)
        for name in ("_Params", "ActionRequest"):
            with self.subTest(cls=name):
                self.assertTrue(
                    _forbids_extra(classes[name]),
                    f"{name} must set model_config = ConfigDict(extra='forbid')",
                )

        schema_names = _param_schema_names(tree)
        self.assertTrue(schema_names, "PARAM_SCHEMAS should map actions to schema classes")

        for name in sorted(schema_names):
            with self.subTest(schema=name):
                self.assertIn(name, classes, f"{name} is referenced by PARAM_SCHEMAS but not defined here")
                node = classes[name]
                bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
                self.assertIn("_Params", bases, f"{name} must inherit _Params so extras stay forbidden")
                offenders = FORBIDDEN_FIELDS & _annotated_fields(node)
                self.assertEqual(offenders, set(), f"{name} exposes command-like field(s): {offenders}")


class NoCommittedSecretTests(unittest.TestCase):
    def test_configuration_contains_no_committed_secrets(self) -> None:
        """(12) No token/secret literal is committed anywhere in the product."""
        suspicious = ("COFFERDAM_TOKEN=", "token=\"", "token='", "secret=\"", "secret='", "password")
        checked = list(_python_sources()) + sorted(WEB_ROOT.glob("*")) + sorted((REPO_ROOT / "deploy").glob("*"))
        offenders = []
        for path in checked:
            if not path.is_file() or path.suffix in (".png", ".jpg", ".jpeg", ".ico"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in suspicious:
                for line in text.splitlines():
                    if marker in line.lower() and "example" not in line.lower():
                        # Assignments of a literal value are the risk; references
                        # to the env var name or a variable are fine.
                        if marker.endswith(("\"", "'")) or "=" in line and marker == "COFFERDAM_TOKEN=":
                            stripped = line.strip()
                            if not stripped.startswith(("#", "*", "//")):
                                offenders.append(f"{path.name}: {stripped[:80]}")
        self.assertEqual(offenders, [], f"possible committed secret: {offenders}")

    def test_secret_paths_are_gitignored(self) -> None:
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignored)

    def test_no_secret_files_are_tracked(self) -> None:
        """Ask git what is tracked — never walk the tree (it contains .venv)."""
        import subprocess

        try:
            completed = subprocess.run(
                ["git", "ls-files"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            raise unittest.SkipTest(f"git unavailable: {exc}")
        if completed.returncode != 0:  # pragma: no cover
            raise unittest.SkipTest("not a git checkout")

        tracked = completed.stdout.decode("utf-8", "replace").splitlines()
        forbidden = [
            name
            for name in tracked
            if Path(name).name in ("token", ".env") or Path(name).suffix in (".pem", ".key")
        ]
        self.assertEqual(forbidden, [], f"secret-like files are tracked: {forbidden}")


class RegistryBoundaryTests(unittest.TestCase):
    """(M2A) Machine registries stay out of Git; example registries stay clean.

    The registries describe a specific person's machines, displays and browser
    habits. Committing one would publish that, and this repository develops in
    public (``DECISIONS.md`` D-2026-08-01-9). The committed placeholders exist
    precisely so nobody is tempted to commit a real one.
    """

    EXAMPLES = REPO_ROOT / "examples" / "registries"
    REGISTRY_FILES = (
        "devices.json",
        "displays.json",
        "applications.json",
        "browser_profiles.json",
        "agent_profiles.json",
        "conversation_routes.json",
    )

    def _tracked(self):
        import subprocess

        try:
            completed = subprocess.run(
                ["git", "ls-files"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            raise unittest.SkipTest(f"git unavailable: {exc}")
        if completed.returncode != 0:  # pragma: no cover
            raise unittest.SkipTest("not a git checkout")
        return completed.stdout.decode("utf-8", "replace").splitlines()

    def test_machine_registries_are_gitignored(self) -> None:
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/registries/", ignored)

    def test_no_machine_registry_is_tracked(self) -> None:
        offenders = [
            name
            for name in self._tracked()
            if "config/registries/" in name.replace("\\", "/")
        ]
        self.assertEqual(offenders, [], f"machine registries are tracked: {offenders}")

    def test_the_example_registries_are_tracked(self) -> None:
        tracked = {name.replace("\\", "/") for name in self._tracked()}
        for name in self.REGISTRY_FILES:
            with self.subTest(example=name):
                self.assertIn(f"examples/registries/{name}", tracked)

    def test_example_registries_carry_no_private_material(self) -> None:
        """(12, extended) The placeholders must stay placeholders."""
        import socket

        hostname = socket.gethostname().lower()
        markers = ["token", "secret", "password", "cookie", "-----begin", "/home/", "@"]
        if len(hostname) > 3:
            markers.append(hostname)
        for path in sorted(self.EXAMPLES.glob("*.json")):
            text = path.read_text(encoding="utf-8").lower()
            for marker in markers:
                with self.subTest(example=path.name, marker=marker):
                    self.assertNotIn(marker, text)

    def test_registry_schemas_declare_no_command_like_field(self) -> None:
        """No schema in the registry package annotates a command-like field."""
        registry_root = PACKAGE_ROOT / "registries"
        self.assertTrue(registry_root.is_dir())
        offenders = []
        for path in sorted(registry_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                found = FORBIDDEN_FIELDS & _annotated_fields(node)
                if found:
                    offenders.append(f"{path.name}:{node.name} {sorted(found)}")
        self.assertEqual(offenders, [], f"registry model exposes command-like field(s): {offenders}")

    def test_the_registry_package_uses_no_subprocess_and_no_shell(self) -> None:
        registry_root = PACKAGE_ROOT / "registries"
        for path in sorted(registry_root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("subprocess", source)
                self.assertNotIn("os.system", source)
                self.assertNotIn("os.popen", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
