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
        adapter runs the Claude Code CLI and its Git observations.
        ``sessions/wrapper.py`` (M2H PR2) runs the native Remote Control child
        and reads its stdout, which is the only way to capture the session link —
        an ``execv`` replacement, which is what PR1 used, has nothing left to
        read with.

        ``claude_agent_sdk/hostclient.py`` (M2I PR2) starts the helper process
        the Agent SDK runs inside. It is here for a reason worth stating rather
        than waving at: the SDK builds its child's environment as
        ``{**os.environ, …, **options.env}``, so there is no supported way to
        hand it a complete environment — and the way to stop an agent inheriting
        the daemon's secrets is for Cofferdam to own the spawn and pass an
        allowlist. The argv is three constants, the environment is built by
        selection in ``hostenv.py``, and ``tests/test_agent_sdk_adapter.py``
        asserts both from the syntax tree.

        The property being protected is not "one file" — it is that a request
        handler, a model, the store, or the service layer can never reach a
        process, and that is still exactly true.

        The companion guard above still applies to every one of these files:
        ``shell=`` must be ``False``, and ``os.system``/``os.popen`` are absent
        from the whole package.
        """
        offenders = []
        for path in _python_sources():
            if path.name == "base.py" and path.parent.name == "adapters":
                continue
            if "claude_code" in path.parts or "claude_code_worker" in path.parts:
                continue
            if path.name == "checks.py" and path.parent.name == "worker":
                # M2L PR1e amendment. The credential-free check sandbox, and the
                # reason it must be host-owned rather than adapter-owned is the
                # finding that produced it: with project code running in the
                # Claude namespace, an allowed `python3` read the provider
                # credential and a socket sent it to a listener.
                #
                # So project code runs here instead, and this file is the only
                # thing that starts it. Narrower than every entry above: the
                # command is a literal tuple looked up by id in a closed table
                # (`CHECK_COMMANDS`) — never caller or model text — `shell=False`,
                # a timeout, an output cap, an environment of seven literal keys,
                # and a namespace with `--unshare-net` and no credential bound.
                # It signals nothing and starts nothing that outlives the call.
                continue
            if path.name == "worktree.py" and path.parent.name == "worker":
                # M2L PR1e. The host's own worktree operations, and the third
                # non-adapter file allowed a process — for exactly the reason
                # `gitbaseline.py` and `gitrange.py` are, one paragraph down.
                #
                # A development worker writes to a repository, and the only safe
                # version of that is one where **Cofferdam** decided where. If
                # the adapter cut its own worktree, the worker's own integration
                # would be choosing the directory a model may edit; the point of
                # this file is that it does not.
                #
                # Narrower than any adapter above it: every argv is a literal
                # list built in the module, `shell=False`, a timeout, the working
                # directory is a root the project registry already verified, and
                # nothing formats, joins or interpolates a caller value into an
                # argument. It signals nothing — no `os.kill`, no `terminate`, no
                # process name — because it starts nothing that outlives the call.
                continue
            if path.name == "wrapper.py" and path.parent.name == "sessions":
                continue
            if path.name == "claude_code.py" and path.parent.name == "providers":
                # M2L PR1c-a. The planner provider, and the narrowest process
                # owner in this file: it starts one CLI, with **no tools at
                # all**, and reads its stdout.
                #
                # Scoped by (name, parent) rather than by filename, so a future
                # `claude_code.py` elsewhere is still caught.
                #
                # What it gives up is smaller than what it keeps. The argv is
                # constants plus host-owned configuration — `tests/
                # test_planner_contracts.py` asserts from the constructed
                # command line that no request text reaches it, that `--tools`
                # is always `""`, and that `--mcp-config` never appears. The
                # request travels on stdin, so user and Custom-GPT prose never
                # enters a command line. `shell=False`, a timeout, a
                # code-owned working directory that is refused if it has
                # acquired a `.mcp.json` or a `CLAUDE.md`, and no child that
                # outlives the call.
                #
                # It signals nothing: `os.kill`, `signal`, `SIGTERM`, `SIGKILL`
                # and `terminate()` are absent, asserted directly in
                # `tests/test_planner_contracts.py` rather than left to habit.
                continue
            if path.name == "hostclient.py" and path.parent.name == "claude_agent_sdk":
                continue
            if (
                path.name in ("gitbaseline.py", "gitrange.py")
                and path.parent.name == "tasks"
            ):
                # M2K PR4 and PR5. The host's own Git probes — the pre-work
                # boundary and the committed range measured from it — and the
                # first non-adapter files allowed a process. Deliberately,
                # because the thing they establish is precisely that the *host*
                # and not the adapter decides what the repository looked like
                # before a worker touched it and what it committed afterwards. An
                # adapter-owned baseline would be a worker describing its own
                # starting line, and an adapter-owned range would be a worker
                # describing its own finish.
                #
                # Both are narrower than any file above them: constant argv
                # tuples checked against a closed set before the call,
                # `shell=False`, an environment built from four literal keys
                # rather than inherited, a timeout, an output cap, and no child
                # that outlives the call. Nothing formats, joins or interpolates
                # a Git argument, and the only values that reach one are resolved
                # object ids that a shape check refuses to let be anything else —
                # `tests/test_git_baseline_authority.py` and
                # `tests/test_git_range_capture.py` assert that from the callable
                # surface.
                continue
            if path.name == "session.py" and path.parent.name == "worker":
                # M2L PR1g. One call: `claude auth status` against Cofferdam's
                # own config root, to answer "is the worker signed in" without
                # reading a credential. Narrower than the Git probes above — a
                # two-element constant argv with no interpolation, `shell=False`,
                # an environment built from four literal keys, `stdin=DEVNULL`, a
                # timeout, and only three named fields kept from the output.
                # `TheNarrowSubprocessExemptions` below asserts each of those
                # rather than trusting this comment.
                continue
            if path.name == "auth.py" and path.parent.name == "worker":
                # M2L PR1g. The one-time operator login, and the only file here
                # that inherits an environment — deliberately, because an
                # interactive sign-in legitimately needs a terminal, a display
                # and a browser opener. It is an operator-invoked entry point,
                # never imported by the daemon (asserted below), and the two
                # variables that decide *which account session* is touched are
                # overridden rather than inherited.
                continue
            source = path.read_text(encoding="utf-8")
            if "subprocess." in source:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"subprocess used outside adapter code: {offenders}")

    def test_the_worker_session_probe_is_as_narrow_as_its_exemption_claims(self):
        """The bound on PR1g's exemption, asserted from the source that runs.

        An allowlist entry is worth what its justification is worth, so the
        claims in the comment above are checked here instead of being believed.
        """
        import inspect

        from cofferdam.workstation.worker import session

        source = inspect.getsource(session.probe)
        self.assertIn('"auth", "status"', source)
        self.assertIn("stdin=subprocess.DEVNULL", source)
        self.assertIn("timeout=timeout", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("Popen", source)
        # The environment is four literal keys, not `os.environ`.
        self.assertNotIn("os.environ", source)
        self.assertIn('"PATH": "/usr/bin:/bin"', source)

    def test_the_worker_auth_tool_is_never_imported_by_the_daemon(self):
        """Its broader exemption is only defensible because nothing loads it."""
        root = Path(__file__).resolve().parents[1] / "cofferdam"
        importers = []
        for path in root.rglob("*.py"):
            if path.name == "auth.py" and path.parent.name == "worker":
                continue
            source = path.read_text(encoding="utf-8")
            if "worker.auth" in source or "from .auth import" in source:
                importers.append(str(path.relative_to(root)))
        self.assertEqual(importers, [], f"the login tool is imported by {importers}")

    def test_the_login_tool_never_handles_a_credential(self):
        """It sets two variables and execs. It does not read what comes back.

        Scans the *code*, with docstrings and comments stripped. The first
        version of this scanned raw text and failed on its own prose — the module
        docstring says it does not type a password, which is exactly the word
        being searched for. A structural check has to look at structure.
        """
        import ast

        from cofferdam.workstation.worker import auth

        tree = ast.parse(Path(auth.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Drop every docstring so prose cannot satisfy or fail this test.
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body = node.body[1:]
        code = ast.unparse(tree)
        for forbidden in (
            "password", "cookie", "accessToken", "refreshToken",
            "credentials.json", "getpass",
        ):
            self.assertNotIn(forbidden, code, forbidden)

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
