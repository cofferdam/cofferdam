"""Structural guards on the service lifecycle (the M1.1 login-loop regression).

Background — what actually happened
-----------------------------------
``deploy/cofferdam-workstation.service`` carried ``Wants=graphical-session.target``
while also being ``WantedBy=default.target``, on a host with
``loginctl enable-linger`` set. Lingering starts ``user@<uid>.service`` at boot,
that manager runs ``default.target``, ``default.target`` pulled Cofferdam in,
and Cofferdam's ``Wants=`` pulled in ``graphical-session.target`` — activating
it at boot with no compositor behind it. gnome-session then refused to start
the session it was supposed to own ("A graphical session is already running!")
and quit, so every graphical login bounced straight back to GDM.

``Wants=`` is an activation request, not a wait. These tests make that class of
mistake impossible to reintroduce silently, by asserting against the shipped
unit files and the source tree rather than against runtime behaviour.

Everything here is standard-library only, so it runs on the stdlib-only CI path.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = REPO_ROOT / "deploy"
PACKAGE_ROOT = REPO_ROOT / "cofferdam"
DOCS_ROOT = REPO_ROOT / "docs"

GRAPHICAL_TARGET = "graphical-session.target"

# Directives that ACTIVATE or bind a unit's fate to another unit. Naming the
# graphical target in any of these from an always-on unit is the regression.
ACTIVATING_DIRECTIVES = (
    "Wants",
    "Requires",
    "Requisite",
    "BindsTo",
    "PartOf",
    "Upholds",
    "WantedBy",
    "RequiredBy",
    "UpheldBy",
)

# Ordering-only directives are safe in principle, but on a unit that may start
# before login they are still wrong: nothing orders against a target that is not
# in the transaction, so it only creates a false impression of protection.
ORDERING_DIRECTIVES = ("After", "Before")

# Environment a headless daemon must never require to start.
GRAPHICAL_ENV_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")

# Commands that would let Cofferdam take the GNOME/GDM lifecycle away from the
# desktop. None of these may appear anywhere we ship.
PROHIBITED_COMMANDS = (
    "systemctl --user exit",
    "systemctl --user stop graphical-session.target",
    "systemctl --user restart graphical-session.target",
    "systemctl --user start graphical-session.target",
    "systemctl --user isolate",
    "loginctl terminate-user",
    "loginctl terminate-session",
    "loginctl kill-user",
    "loginctl kill-session",
    "gnome-session-quit",
)

# Broad process killers: they cannot distinguish a Cofferdam-owned process from
# the user's own browser or shell, so they are banned outright.
BROAD_KILLERS = ("pkill", "killall")


def _python_code_only(source: str) -> str:
    """Python source with comments and docstrings removed."""
    import ast
    import io
    import tokenize

    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - defensive
        return source
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                docstrings.add((first.lineno, first.col_offset))
    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and token.start in docstrings:
            continue
        kept.append(token.string)
    return "\n".join(kept)

SECRET_PATTERNS = (
    re.compile(r"(?i)\btoken\s*=\s*[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key)\s*=\s*\S{8,}"),
)

WILDCARD_BINDS = ("0.0.0.0", "::", "*:")


def _unit_files() -> List[Path]:
    return sorted(DEPLOY_ROOT.glob("*.service"))


def _shipped_scripts() -> List[Path]:
    return sorted(DEPLOY_ROOT.glob("*.sh"))


def _python_sources() -> List[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _strip_comments(text: str) -> str:
    """Unit-file directives only — systemd treats ``#``/``;`` lines as comments.

    This matters: the corrected unit *documents* the forbidden directives at
    length so the next maintainer understands why they are absent. Those
    explanations must not be mistaken for the directives themselves.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith(";"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _fenced_code(text: str) -> List[str]:
    """Lines inside ``` fenced blocks — the parts of a document you can run."""
    lines: List[str] = []
    inside = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside:
            lines.append(line)
    return lines


def _directives(text: str) -> List[tuple]:
    """(key, value) for every real directive line, comments removed."""
    found = []
    for line in _strip_comments(text).splitlines():
        key, separator, value = line.partition("=")
        if separator:
            found.append((key.strip(), value.strip()))
    return found


def _sections(text: str) -> Dict[str, List[tuple]]:
    """Directives grouped by ``[Section]``."""
    sections: Dict[str, List[tuple]] = {}
    current = ""
    for line in _strip_comments(text).splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            sections.setdefault(current, [])
            continue
        key, separator, value = line.partition("=")
        if separator:
            sections.setdefault(current, []).append((key.strip(), value.strip()))
    return sections


class UnitFilePresenceTests(unittest.TestCase):
    def test_the_workstation_unit_is_shipped(self) -> None:
        """Guard against the whole suite silently passing on an empty glob."""
        names = {path.name for path in _unit_files()}
        self.assertIn("cofferdam-workstation.service", names)


class GraphicalTargetCouplingTests(unittest.TestCase):
    """The regression itself: no shipped unit may pull the graphical target."""

    def test_no_unit_activates_the_graphical_target(self) -> None:
        for unit in _unit_files():
            with self.subTest(unit=unit.name):
                for key, value in _directives(unit.read_text(encoding="utf-8")):
                    if key in ACTIVATING_DIRECTIVES and GRAPHICAL_TARGET in value:
                        self.fail(
                            f"{unit.name} declares {key}={value}. "
                            f"{key}= puts {GRAPHICAL_TARGET} into the start transaction, "
                            "which activates it with no session behind it when the unit "
                            "starts at boot under lingering. That is the login loop."
                        )

    def test_an_always_on_unit_does_not_order_against_the_graphical_target(self) -> None:
        """A unit reachable from default.target must not even order against it."""
        for unit in _unit_files():
            text = unit.read_text(encoding="utf-8")
            sections = _sections(text)
            install = sections.get("Install", [])
            always_on = any(
                key == "WantedBy" and "default.target" in value for key, value in install
            )
            if not always_on:
                continue
            with self.subTest(unit=unit.name):
                for key, value in _directives(text):
                    if key in ORDERING_DIRECTIVES and GRAPHICAL_TARGET in value:
                        self.fail(
                            f"{unit.name} is WantedBy=default.target but declares "
                            f"{key}={value}. An always-on unit cannot order against a "
                            "target that is not in its transaction; detect the session "
                            "at request time instead."
                        )

    def test_a_graphical_unit_is_never_wanted_by_default_target(self) -> None:
        """If a session-scoped unit is ever added, it must not be always-on."""
        for unit in _unit_files():
            text = unit.read_text(encoding="utf-8")
            sections = _sections(text)
            session_scoped = any(
                key in ("PartOf", "WantedBy", "BindsTo") and GRAPHICAL_TARGET in value
                for key, value in _directives(text)
            )
            if not session_scoped:
                continue
            with self.subTest(unit=unit.name):
                for key, value in sections.get("Install", []):
                    if key == "WantedBy" and "default.target" in value:
                        self.fail(
                            f"{unit.name} is tied to {GRAPHICAL_TARGET} but is also "
                            "WantedBy=default.target. A graphical component must not be "
                            "a pre-login always-on unit."
                        )

    def test_nothing_shipped_starts_or_stops_the_graphical_target(self) -> None:
        pattern = re.compile(
            r"systemctl[^\n;|&]*\b(start|stop|restart|isolate|reload-or-restart)\b[^\n;|&]*"
            + re.escape(GRAPHICAL_TARGET)
        )
        for path in _unit_files() + _shipped_scripts() + _python_sources():
            with self.subTest(path=path.name):
                self.assertIsNone(
                    pattern.search(path.read_text(encoding="utf-8")),
                    f"{path.name} tries to drive {GRAPHICAL_TARGET}. Cofferdam follows "
                    "that target; GNOME owns it.",
                )

    def test_the_adapter_queries_the_target_read_only(self) -> None:
        """Detection must use is-active/show — never a command that activates."""
        source = (PACKAGE_ROOT / "workstation" / "adapters" / "linux_session.py").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            source,
            r'"(is-active|show)"',
            "the session adapter must query the graphical target read-only",
        )
        for verb in ("start", "isolate", "stop", "restart"):
            self.assertNotIn(
                f'SYSTEMCTL, "--user", "{verb}"',
                source,
                f"the session adapter must never {verb} a target or unit it does not own",
            )


class HeadlessDaemonTests(unittest.TestCase):
    """The daemon must be able to start with no desktop at all."""

    def test_no_unit_requires_graphical_environment(self) -> None:
        for unit in _unit_files():
            with self.subTest(unit=unit.name):
                for key, value in _directives(unit.read_text(encoding="utf-8")):
                    if key not in ("Environment", "EnvironmentFile"):
                        continue
                    for variable in GRAPHICAL_ENV_VARS:
                        self.assertNotIn(
                            variable + "=",
                            value,
                            f"{unit.name} pins {variable} in the unit. A daemon that "
                            "starts before login would freeze a value that is either "
                            "absent or belongs to a session that has since ended; the "
                            "adapter reads the user manager's live environment instead.",
                        )

    def test_the_daemon_does_not_read_graphical_env_to_decide_it_can_start(self) -> None:
        """Entry point must not gate startup on desktop variables."""
        source = (PACKAGE_ROOT / "workstation" / "__main__.py").read_text(encoding="utf-8")
        for variable in GRAPHICAL_ENV_VARS:
            self.assertNotIn(
                variable,
                source,
                f"the entry point references {variable}; the headless daemon must start "
                "without any graphical session.",
            )


class RestartPolicyTests(unittest.TestCase):
    """A permanent failure must not become an unbounded respawn storm."""

    def test_restart_rate_is_limited(self) -> None:
        for unit in _unit_files():
            text = unit.read_text(encoding="utf-8")
            directives = dict(_directives(text))
            if directives.get("Restart", "no") in ("no", "never"):
                continue
            with self.subTest(unit=unit.name):
                interval = directives.get("StartLimitIntervalSec")
                self.assertIsNotNone(
                    interval,
                    f"{unit.name} restarts but sets no StartLimitIntervalSec.",
                )
                self.assertNotIn(
                    interval,
                    ("0", "0s", "infinity"),
                    f"{unit.name} sets StartLimitIntervalSec={interval}, which disables "
                    "the rate limiter entirely — a permanent failure then respawns "
                    "forever.",
                )
                self.assertIn(
                    "StartLimitBurst",
                    directives,
                    f"{unit.name} restarts but sets no StartLimitBurst.",
                )
                delay = directives.get("RestartSec", "0")
                self.assertGreaterEqual(
                    float(re.sub(r"[^0-9.]", "", delay) or 0),
                    1.0,
                    f"{unit.name} sets RestartSec={delay}: too tight to be safe.",
                )

    def test_start_limit_directives_are_in_the_unit_section(self) -> None:
        """A rate limit in the wrong section is silently ignored.

        systemd reads ``StartLimitIntervalSec``/``StartLimitBurst`` from
        ``[Unit]``. Put them under ``[Service]`` and it logs
        "Unknown key ... ignoring" and carries on with no rate limit at all —
        so the unit looks protected while the restart storm it was meant to
        stop is still possible. Caught exactly that way on a real host.
        """
        for unit in _unit_files():
            sections = _sections(unit.read_text(encoding="utf-8"))
            service_keys = {key for key, _ in sections.get("Service", [])}
            with self.subTest(unit=unit.name):
                for key in ("StartLimitIntervalSec", "StartLimitBurst"):
                    self.assertNotIn(
                        key,
                        service_keys,
                        f"{unit.name} puts {key} under [Service], where systemd ignores "
                        "it. It belongs in [Unit].",
                    )


class ProcessTerminationTests(unittest.TestCase):
    """Cofferdam may never terminate a session, a manager, or a broad match."""

    def _shipped_text(self):
        for path in _unit_files() + _shipped_scripts() + _python_sources() + sorted(
            DOCS_ROOT.rglob("*.md")
        ):
            yield path, path.read_text(encoding="utf-8")

    def test_no_prohibited_lifecycle_command_is_shipped(self) -> None:
        """None of these may appear as something a reader or machine would run.

        Documentation is held to a different standard than code, on purpose:
        ``docs/SERVICE_LIFECYCLE.md`` and ``SECURITY.md`` have to *name* these
        commands in order to forbid them. What they must never do is present one
        in a fenced code block, which is the form a reader copies and runs. So
        prose is allowed and runnable blocks are not.
        """
        for path, text in self._shipped_text():
            if path.suffix == ".md":
                body = "\n".join(_fenced_code(text))
            elif path.suffix == ".service":
                body = _strip_comments(text)
            else:
                body = text
            for command in PROHIBITED_COMMANDS:
                for line in body.splitlines():
                    if command not in line:
                        continue
                    with self.subTest(path=path.name, command=command):
                        self.fail(
                            f"{path.name} presents {command!r} as a command to run: "
                            f"{line.strip()!r}. Cofferdam follows the GNOME session "
                            "lifecycle; it never drives it."
                        )

    def test_no_broad_process_killer_is_shipped(self) -> None:
        for path, text in self._shipped_text():
            if path.suffix == ".md":
                continue
            if path.suffix == ".service":
                body = _strip_comments(text)
            elif path.suffix == ".py":
                # Code only. A module that documents at length why it never
                # uses `pkill` must not fail the check that it never uses
                # `pkill` — the repair for that would be deleting the
                # explanation, which is exactly backwards. Same technique the
                # task guards use; see `tests/_task_doubles.python_code_only`.
                body = _python_code_only(text)
            else:
                body = text
            for killer in BROAD_KILLERS:
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if re.search(r"\b" + killer + r"\b", stripped):
                        self.fail(
                            f"{path.name} uses {killer}: {stripped!r}. It matches by name "
                            "and cannot tell a Cofferdam process from the user's own."
                        )

    def test_source_never_signals_a_process_it_did_not_verify(self) -> None:
        """No module signals a process whose identity it did not establish.

        The rule this enforces is about *applications*: Spotify, Opera, a media
        player. Those are started as transient systemd units, the user manager
        owns their cgroups, and Cofferdam signalling them directly would be
        reaching past the thing that actually knows what they are.

        A task adapter's child is a different kind of process and the reasoning
        does not carry over. Cofferdam forked it, holds its ``Popen``, recorded
        its start time at exec, and put it in a process group it created. There
        is no manager to defer to, and the only thing that could stop it is the
        code that started it.

        So that one package is excepted here, and the exception is paid for in
        ``tests/test_claude_code_adapter.py``, which asserts the part this scan
        cannot see: every signal is preceded by a fresh pid + start-time +
        process-group check, and removing that check fails a test.
        """
        for path in _python_sources():
            if "claude_code" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for forbidden in ("os.kill(", "SIGKILL", "process.terminate(", "proc.kill("):
                    self.assertNotIn(
                        forbidden,
                        text,
                        f"{path.name} signals a process directly. Application lifetime "
                        "belongs to the systemd user manager, which owns each transient "
                        "unit's cgroup.",
                    )


class BindingTests(unittest.TestCase):
    """The listener stays on a private address; no wildcard fallback exists."""

    def test_no_wildcard_bind_is_configured(self) -> None:
        paths = _unit_files() + _shipped_scripts() + [DEPLOY_ROOT / "workstation.env.example"]
        for path in paths:
            if not path.is_file():
                continue
            body = _strip_comments(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.name):
                for wildcard in WILDCARD_BINDS:
                    for line in body.splitlines():
                        if "BIND_HOST" in line and wildcard in line and not line.strip().startswith("#"):
                            self.fail(f"{path.name} configures a wildcard bind: {line.strip()!r}")

    def test_the_entry_point_never_falls_back_to_a_wildcard(self) -> None:
        """No wildcard address may appear as a *value* anywhere in the daemon.

        Checked against real string literals rather than raw text: the module
        docstring explains why a wildcard is never used, and prose that forbids
        something must not be mistaken for doing it.

        ``"0.0.0.0"`` and ``"::"`` are addresses and nothing else, so they are
        banned everywhere. A bare ``"*"`` is not — it is an ordinary character
        in unrelated code, and the registry domain validator legitimately tests
        ``if "*" in host`` in order to **reject** wildcard domains. Banning it
        outside the modules that actually bind would fail on code doing the
        right thing, so it is checked only where a bind address can be chosen.
        The narrowing is deliberate and is itself pinned by
        ``test_a_wildcard_bind_would_still_be_caught`` below.
        """
        binding_modules = {"__main__.py", "config.py", "service.py"}
        for path in _python_sources():
            wildcards = {"0.0.0.0", "::"} | ({"*"} if path.name in binding_modules else set())
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
                        node.value,
                        wildcards,
                        f"{path.name}:{node.lineno} contains the wildcard address "
                        f"{node.value!r}. The service binds only to its configured "
                        "private address and must never fall back to a public one.",
                    )

    def test_a_wildcard_bind_would_still_be_caught(self) -> None:
        """The guard above is narrowed for `"*"`; prove it still has teeth.

        Each of these is what a real regression would look like. Every one must
        be rejected, or the narrowing has quietly disabled the protection.
        """
        regressions = (
            ('host = "0.0.0.0"', "models.py", "an IPv4 wildcard anywhere in the daemon"),
            ('host = "::"', "models.py", "an IPv6 wildcard anywhere in the daemon"),
            ('bind = "*"', "__main__.py", "a bare wildcard in a binding module"),
            ('fallback = "0.0.0.0"', "__main__.py", "a wildcard fallback in the entry point"),
        )
        binding_modules = {"__main__.py", "config.py", "service.py"}

        for source, filename, description in regressions:
            with self.subTest(regression=description):
                wildcards = {"0.0.0.0", "::"} | ({"*"} if filename in binding_modules else set())
                found = {
                    node.value
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                }
                self.assertTrue(
                    found & wildcards,
                    f"the narrowed guard would not catch {description}",
                )

        # And the legitimate pattern the narrowing exists for stays accepted.
        legitimate = 'if "*" in host or "?" in host:\n    raise Rejected("wildcards are not accepted")'
        found = {
            node.value
            for node in ast.walk(ast.parse(legitimate))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertFalse(
            found & {"0.0.0.0", "::"},
            "rejecting wildcard domains must not look like configuring a wildcard bind",
        )

    def test_a_missing_bind_address_is_waited_for_and_then_given_up_on(self) -> None:
        source = (PACKAGE_ROOT / "workstation" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("wait_for_bind_address", source)
        self.assertIn("DEFAULT_BIND_WAIT_SECONDS", source)


class SecretsInUnitsTests(unittest.TestCase):
    def test_no_unit_or_script_embeds_a_secret(self) -> None:
        for path in _unit_files() + _shipped_scripts():
            body = _strip_comments(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.name):
                for pattern in SECRET_PATTERNS:
                    self.assertIsNone(
                        pattern.search(body),
                        f"{path.name} looks like it embeds a secret. The device token is "
                        "read from a 0600 file at runtime.",
                    )


class InstallerSafetyTests(unittest.TestCase):
    """Installer and uninstaller may only ever touch Cofferdam-owned paths."""

    SENSITIVE_TARGETS = (
        "~/.config",
        "$HOME/.config",
        "~/.local",
        "$HOME/.local",
        "~/.cache",
        "$HOME/.cache",
        "dconf",
    )

    def test_scripts_are_shipped(self) -> None:
        names = {path.name for path in _shipped_scripts()}
        self.assertIn("install-workstation-service.sh", names)
        self.assertIn("uninstall-workstation-service.sh", names)

    def test_no_script_removes_a_user_configuration_tree(self) -> None:
        """No rm/mv may target a config tree — only our own unit and symlinks."""
        destructive = re.compile(r"\b(rm|rmdir|mv|shred|find)\b[^\n]*")
        for path in _shipped_scripts():
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                match = destructive.search(stripped)
                if not match:
                    continue
                fragment = match.group(0)
                with self.subTest(path=path.name, line=line_number):
                    for target in self.SENSITIVE_TARGETS:
                        if target in fragment:
                            # Allowed only when it is clearly scoped to our unit
                            # directory AND names our unit.
                            self.assertIn(
                                "cofferdam",
                                fragment.lower(),
                                f"{path.name}:{line_number} removes {target} without "
                                f"scoping to a Cofferdam-owned path: {fragment!r}",
                            )

    def test_uninstaller_resolves_symlinks_before_removing_them(self) -> None:
        """A name match alone is not enough to justify unlinking."""
        text = (DEPLOY_ROOT / "uninstall-workstation-service.sh").read_text(encoding="utf-8")
        self.assertIn("readlink", text)
        self.assertIn("UNIT_NAME", text)

    def test_no_script_resets_dconf_or_gnome_settings(self) -> None:
        for path in _shipped_scripts():
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                with self.subTest(path=path.name):
                    for command in ("dconf ", "gsettings ", "gnome-extensions "):
                        self.assertNotIn(
                            command,
                            stripped,
                            f"{path.name} changes desktop settings: {stripped!r}",
                        )

    def test_no_script_enables_automatic_login(self) -> None:
        for path in _shipped_scripts():
            text = path.read_text(encoding="utf-8").lower()
            for marker in ("automaticlogin", "autologin"):
                for line in text.splitlines():
                    if marker in line and not line.strip().startswith("#"):
                        self.fail(f"{path.name} touches automatic login: {line.strip()!r}")

    def test_installer_refuses_a_unit_that_names_the_graphical_target(self) -> None:
        """The migration validates before enabling, not after."""
        text = (DEPLOY_ROOT / "install-workstation-service.sh").read_text(encoding="utf-8")
        self.assertIn(GRAPHICAL_TARGET, text)
        self.assertIn("systemd-analyze", text)
        self.assertIn("Refusing to enable", text)


if __name__ == "__main__":
    unittest.main()
