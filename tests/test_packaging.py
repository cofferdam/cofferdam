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
