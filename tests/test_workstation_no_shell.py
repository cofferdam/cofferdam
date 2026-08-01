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


def _python_sources():
    return sorted(PACKAGE_ROOT.rglob("*.py"))


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
        """Subprocess use is centralized in ``adapters/base.py``."""
        offenders = []
        for path in _python_sources():
            if path.name == "base.py" and path.parent.name == "adapters":
                continue
            source = path.read_text(encoding="utf-8")
            if "subprocess." in source:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"subprocess used outside adapters/base.py: {offenders}")

    def test_action_schemas_expose_no_command_like_field(self) -> None:
        from cofferdam.workstation.actions import PARAM_SCHEMAS

        forbidden = {"command", "cmd", "args", "argv", "shell", "exec", "executable", "path", "script"}
        for action, schema in PARAM_SCHEMAS.items():
            with self.subTest(action=action):
                self.assertEqual(set(schema.model_fields) & forbidden, set())
                self.assertEqual(schema.model_config.get("extra"), "forbid")


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
