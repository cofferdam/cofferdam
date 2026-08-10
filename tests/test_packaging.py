"""The distribution declares every package it actually ships.

This file exists because of a bug that shipped and was never noticed. The
`[tool.setuptools] packages` list is explicit — which is the right choice, since
auto-discovery would sweep up `tests` — but `cofferdam.workstation.tasks` was
added in M2F and its Claude adapter in M2G, and neither was ever added to the
list. A wheel built from that tree contained no Task Core at all.

Nothing caught it because the documented install is **editable**
(`pip install -e ".[workstation]"`, see `docs/host-setup.md`). An editable
install puts the source tree on `sys.path`, so every package imports whether or
not it is declared, and the declaration only matters when somebody builds a real
wheel.

So the test is not "does it import" — on this machine it always will. The test
is that the *declaration* matches the tree.

Standard-library only, and it neither builds nor installs anything: it compares
two lists of names. The wheel build and temporary-venv install that prove the
same property end-to-end are a manual validation step, recorded in the pull
request rather than run on every CI job.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import List, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PACKAGE_ROOT = REPO_ROOT / "cofferdam"


def _declared_packages() -> List[str]:
    """The `packages = [...]` list from pyproject.toml.

    Parsed with ``tomllib`` where it exists, and with a small bracket scan
    otherwise, so this runs on every interpreter the project supports rather
    than skipping — and skipping is how a packaging test passes while the
    packaging is broken.
    """
    try:
        import tomllib

        with PYPROJECT.open("rb") as handle:
            return list(tomllib.load(handle)["tool"]["setuptools"]["packages"])
    except ImportError:
        text = PYPROJECT.read_text(encoding="utf-8")
        start = text.index("packages = [")
        body = text[start : text.index("]", start)]
        return re.findall(r'"([^"]+)"', body)


def _source_packages() -> Set[str]:
    """Every importable package in the source tree, by dotted name."""
    found = set()
    for init in PACKAGE_ROOT.rglob("__init__.py"):
        relative = init.parent.relative_to(REPO_ROOT)
        found.add(".".join(relative.parts))
    return found


class DeclaredPackagesTests(unittest.TestCase):
    def test_every_source_package_is_declared(self) -> None:
        missing = sorted(_source_packages() - set(_declared_packages()))
        self.assertEqual(
            missing,
            [],
            "these packages exist in the tree but would be missing from a built "
            "wheel; add them to [tool.setuptools] packages in pyproject.toml: "
            + ", ".join(missing),
        )

    def test_every_declared_package_exists(self) -> None:
        """The other direction: a renamed package must not leave a stale entry."""
        stale = sorted(set(_declared_packages()) - _source_packages())
        self.assertEqual(stale, [], "declared but absent from the tree: %s" % stale)

    def test_the_declaration_is_explicit_not_discovered(self) -> None:
        """No `find:` directive — `tests` must never end up in the wheel."""
        directives = [
            line.strip()
            for line in PYPROJECT.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        ]
        for line in directives:
            with self.subTest(line=line):
                self.assertNotIn("find:", line)
                self.assertNotIn("[tool.setuptools.packages.find]", line)
        self.assertNotIn("tests", _declared_packages())

    def test_the_lane_a_and_task_core_packages_are_declared(self) -> None:
        """Named individually: these two are what the systemd unit imports.

        `cofferdam-rc@.service` runs `-m cofferdam.workstation.sessions.host`,
        which imports `cofferdam.workstation.tasks.projects` to resolve the
        registered project. A wheel missing either produces a unit that fails at
        import with a traceback in the journal.
        """
        declared = _declared_packages()
        for required in (
            "cofferdam.workstation.sessions",
            "cofferdam.workstation.tasks",
        ):
            with self.subTest(package=required):
                self.assertIn(required, declared)

    def test_the_declaration_is_sorted(self) -> None:
        """Keeps the diff for a new subpackage to one line."""
        declared = _declared_packages()
        self.assertEqual(declared, sorted(declared))


def _declared_extras() -> dict:
    """`[project.optional-dependencies]` as {extra: [requirement, ...]}."""
    try:
        import tomllib

        with PYPROJECT.open("rb") as handle:
            return dict(tomllib.load(handle)["project"]["optional-dependencies"])
    except ImportError:
        text = PYPROJECT.read_text(encoding="utf-8")
        section = text[text.index("[project.optional-dependencies]") :]
        section = section[: section.index("\n[")]
        extras = {}
        for match in re.finditer(r"^(\S+) = \[(.*?)^\]", section, re.MULTILINE | re.DOTALL):
            extras[match.group(1)] = re.findall(r'"([^"]+)"', match.group(2))
        return extras


def _third_party_imports(package: Path) -> Set[str]:
    """Top-level modules a package imports that are not stdlib and not its own.

    Parsed with :mod:`ast` rather than matched with a regex. A regex over lines
    beginning ``import``/``from`` also matches prose — a docstring sentence that
    happens to start "from anywhere in the tree" reads as an import of a package
    called ``anywhere`` — and a packaging guard that reports imaginary
    dependencies is one people learn to ignore.

    ``ast`` parses source text without importing it, so this still runs on the
    stdlib-only path where the package's own extras are absent.
    """
    import ast
    import sys

    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    found: Set[str] = set()
    for source in sorted(package.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # `level > 0` is a relative import: the package's own modules,
                # which no requirement provides and none should.
                names = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for dotted in names:
                top = dotted.split(".")[0]
                if not top or top in stdlib or top in ("__future__", "cofferdam"):
                    continue
                found.add(top)
    return found


class ExtraDependencyTests(unittest.TestCase):
    """Every runtime import is declared by the extra that installs it.

    The bug this was written for: `cofferdam/actions_bridge/internal.py` imports
    httpx at module scope — it is the client the bridge uses to reach the daemon
    — but httpx was declared only under the test-only `dev` extra. A clean
    `pip install -e ".[workstation]"` produced a bridge that started, passed its
    own `--check`, and then raised ImportError on the first request that needed
    the daemon.

    Nothing caught it because every machine that had ever run the bridge also
    had the test extras installed. That is the general shape of this class of
    defect, so the assertion is general: compare the imports against the
    declaration, for every optional package Cofferdam ships.
    """

    #: Import name to the distribution that provides it, where they differ.
    DISTRIBUTION_FOR_IMPORT = {
        "starlette": "fastapi",  # a FastAPI dependency, never declared directly
        "uvicorn": "uvicorn",
        "yaml": "pyyaml",
        "claude_agent_sdk": "claude-agent-sdk",
    }

    def _declared_distributions(self, *extras: str) -> Set[str]:
        declared = _declared_extras()
        names = set()
        for extra in extras:
            for requirement in declared.get(extra, []):
                # "uvicorn[standard]>=0.27; python_version >= '3.10'" -> "uvicorn"
                names.add(re.split(r"[\[><=!;\s]", requirement, maxsplit=1)[0].strip().lower())
        return names

    def test_the_actions_bridge_extra_exists(self) -> None:
        self.assertIn("actions-bridge", _declared_extras())

    def test_every_bridge_import_is_declared_by_its_extra(self) -> None:
        imports = _third_party_imports(PACKAGE_ROOT / "actions_bridge")
        declared = self._declared_distributions("actions-bridge")
        for name in sorted(imports):
            distribution = self.DISTRIBUTION_FOR_IMPORT.get(name, name).lower()
            with self.subTest(imports=name):
                self.assertIn(
                    distribution,
                    declared,
                    f"cofferdam/actions_bridge imports {name!r}, which no "
                    f"requirement in the 'actions-bridge' extra provides. A host "
                    f"that installed only that extra would fail at run time.",
                )

    def test_httpx_is_a_runtime_dependency_not_a_test_one(self) -> None:
        """The specific regression, asserted by name so it cannot come back."""
        self.assertIn("httpx", self._declared_distributions("actions-bridge"))

    def test_the_bridge_entry_point_checks_its_dependencies(self) -> None:
        """`--check` must not pass on a host where the bridge cannot import."""
        source = (PACKAGE_ROOT / "actions_bridge" / "__main__.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("RUNTIME_DEPENDENCIES", source)
        self.assertIn("httpx", source)
        # The check has to happen before `--check` returns success, or it
        # answers a different question than the one being asked.
        self.assertLess(
            source.index("_missing_runtime_dependencies()"),
            source.index("if args.check:"),
        )

    def test_the_agent_sdk_stays_out_of_every_other_extra(self) -> None:
        """Gate B is a separate decision; no other extra may pull it in."""
        for extra in ("workstation", "actions-bridge", "dev"):
            with self.subTest(extra=extra):
                self.assertNotIn(
                    "claude-agent-sdk", self._declared_distributions(extra)
                )


class EntryPointTests(unittest.TestCase):
    def test_the_unit_invokes_a_module_that_exists(self) -> None:
        """The template's `-m` target must be a real, declared module.

        Guards the failure this follow-up fixed: a unit whose ExecStart names a
        module that a normal install does not contain.
        """
        template = (REPO_ROOT / "deploy" / "cofferdam-rc@.service").read_text(
            encoding="utf-8"
        )
        match = re.search(r"^ExecStart=.*?-m\s+(\S+)", template, re.MULTILINE)
        self.assertIsNotNone(match, "the template must invoke a module with -m")
        module = match.group(1)

        parts = module.split(".")
        self.assertEqual(
            (REPO_ROOT.joinpath(*parts).with_suffix(".py")).is_file(),
            True,
            "%s is not a module in this tree" % module,
        )
        self.assertIn(".".join(parts[:-1]), _declared_packages())


if __name__ == "__main__":
    unittest.main()
