"""What a development worker can reach, and what it provably cannot.

The rule for every test in this file: **prove the thing exists first, then prove
it is excluded.** A test that only asserts an absence passes just as happily when
the fixture was never built, and an isolation suite that can pass vacuously is
worse than none — it converts an untested property into a documented one.

So each isolation test writes a real marker into a real second project, checks it
is there, and only then checks that the worker's authorized view does not contain
it.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.tasks.adapters import build_registry
from cofferdam.workstation.tasks.adapters.claude_code_worker import (
    ADAPTER_ID,
    PROMPT_SEPARATOR,
    ClaudeCodeWorkerAdapter,
    build_worker_payload,
    cli,
    delivered_prompt,
)
from cofferdam.workstation.tasks.identity import new_task_id
from cofferdam.workstation.worker import sandbox, worktree


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _no_worker(original):
    """A ``Popen`` that allows Cofferdam's own Git and forbids a worker launch.

    Patching ``Popen`` outright would also break the worktree module's ``git``
    calls, and a test that cannot tell those apart proves nothing about the
    worker. This one refuses only a contained launch — the ``bwrap`` vector —
    which is the thing that must not happen.
    """

    def guarded(argv, *args, **kwargs):
        first = str(argv[0]) if argv else ""
        if "bwrap" in first:
            raise AssertionError("a worker was launched when it should not have been")
        return original(argv, *args, **kwargs)

    return guarded


def make_repo(path: Path, marker: str) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / f"{marker}.txt").write_text(f"belongs to {marker}\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path


class ContainmentHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        self.repo_a = make_repo(self.dir / "alpha", "PROJECT_A")
        self.repo_b = make_repo(self.dir / "beta", "PROJECT_B")

    def cut(self, repo: Path, project_id: str) -> worktree.DevelopmentWorktree:
        return worktree.prepare(
            project_id=project_id, project_root=repo.resolve(),
            task_id=new_task_id(), state_dir=self.state,
        )


# -- the worktree --------------------------------------------------------------


class WorktreeIsCodeOwned(ContainmentHarness):
    def test_a_worktree_is_created_on_a_code_owned_branch(self):
        tree = self.cut(self.repo_a, "alpha")
        self.assertTrue(tree.path.is_dir())
        self.assertTrue(tree.branch.startswith("cofferdam/worker/task_"))
        self.assertEqual(git(tree.path, "rev-parse", "--abbrev-ref", "HEAD"), tree.branch)

    def test_it_lives_outside_the_project_checkout(self):
        tree = self.cut(self.repo_a, "alpha")
        self.assertNotIn(str(self.repo_a.resolve()), str(tree.path))
        self.assertIn("worker-worktrees", str(tree.path))

    def test_the_canonical_checkout_is_untouched(self):
        """Untouched means: same branch, same commit, same clean status."""
        before = worktree.canonical_state(self.repo_a.resolve())
        self.cut(self.repo_a, "alpha")
        self.assertEqual(worktree.canonical_state(self.repo_a.resolve()), before)
        self.assertEqual(before["branch"], "main")
        self.assertEqual(before["status"], "")

    def test_main_is_never_the_worker_branch(self):
        tree = self.cut(self.repo_a, "alpha")
        self.assertNotEqual(tree.branch, "main")
        self.assertNotIn(tree.branch, worktree.FORBIDDEN_BRANCHES)

    def test_no_function_here_takes_a_path_or_a_branch(self):
        parameters = set(inspect.signature(worktree.prepare).parameters)
        self.assertEqual(
            parameters, {"project_id", "project_root", "task_id", "state_dir"}
        )
        for forbidden in ("branch", "base_ref", "worktree_path", "destination", "ref"):
            self.assertNotIn(forbidden, parameters)

    def test_model_text_cannot_become_a_ref(self):
        for hostile in ("main", "../../etc", "feature; rm -rf /", "", "HEAD"):
            with self.assertRaises(worktree.WorktreeError):
                worktree.branch_name(hostile)

    def test_a_bad_project_id_cannot_escape_the_worktree_root(self):
        for hostile in ("../beta", "/etc", "a/../../b"):
            with self.assertRaises(worktree.WorktreeError):
                worktree.worktree_path(self.state, hostile, new_task_id())

    def test_preparing_twice_returns_the_same_worktree(self):
        task_id = new_task_id()
        first = worktree.prepare(
            project_id="alpha", project_root=self.repo_a.resolve(),
            task_id=task_id, state_dir=self.state,
        )
        second = worktree.prepare(
            project_id="alpha", project_root=self.repo_a.resolve(),
            task_id=task_id, state_dir=self.state,
        )
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.branch, second.branch)

    def test_a_non_repository_project_is_refused(self):
        plain = self.dir / "not-a-repo"
        plain.mkdir()
        with self.assertRaises(worktree.WorktreeError):
            worktree.prepare(
                project_id="alpha", project_root=plain, task_id=new_task_id(),
                state_dir=self.state,
            )

    def test_the_worktree_read_model_carries_no_path(self):
        tree = self.cut(self.repo_a, "alpha")
        rendered = repr(tree.to_dict())
        self.assertNotIn(str(tree.path), rendered)
        self.assertNotIn(str(self.repo_a), rendered)


class WorktreesAreProjectScoped(ContainmentHarness):
    """Project B exists and holds a marker — then A's worktree does not have it."""

    def test_project_b_holds_its_own_marker(self):
        self.assertTrue((self.repo_b / "PROJECT_B.txt").is_file())

    def test_a_worktree_contains_only_as_files(self):
        tree = self.cut(self.repo_a, "alpha")
        self.assertTrue((tree.path / "PROJECT_A.txt").is_file())
        self.assertFalse((tree.path / "PROJECT_B.txt").exists())

    def test_two_projects_get_separate_worktree_roots(self):
        a = self.cut(self.repo_a, "alpha")
        b = self.cut(self.repo_b, "beta")
        self.assertNotEqual(a.path, b.path)
        self.assertNotEqual(a.path.parent, b.path.parent)

    def test_project_b_is_unchanged_by_work_in_a(self):
        before = worktree.canonical_state(self.repo_b.resolve())
        tree = self.cut(self.repo_a, "alpha")
        (tree.path / "new.txt").write_text("work happened\n")
        git(tree.path, "add", "-A")
        git(tree.path, "commit", "-qm", "worker change")
        self.assertEqual(worktree.canonical_state(self.repo_b.resolve()), before)
        self.assertFalse((self.repo_b / "new.txt").exists())

    def test_a_commit_lands_on_the_worker_branch_and_not_on_main(self):
        tree = self.cut(self.repo_a, "alpha")
        main_before = git(self.repo_a, "rev-parse", "main")
        (tree.path / "new.txt").write_text("work happened\n")
        git(tree.path, "add", "-A")
        git(tree.path, "commit", "-qm", "worker change")
        self.assertEqual(git(self.repo_a, "rev-parse", "main"), main_before)
        self.assertNotEqual(git(tree.path, "rev-parse", "HEAD"), main_before)


# -- the sandbox ---------------------------------------------------------------


class SandboxPlanIsBounded(ContainmentHarness):
    def plan(self, tree=None):
        tree = tree or self.cut(self.repo_a, "alpha")
        home = sandbox.build_home(self.dir / "home")
        return tree, sandbox.build_plan(
            worktree=tree.path, home=home, cli_directory=Path("/usr"),
            command=("/bin/echo", "hello"),
        )

    def test_only_the_worktree_and_the_worker_home_are_writable(self):
        tree, plan = self.plan()
        writable = {
            plan.argv[i + 1] for i, token in enumerate(plan.argv) if token == "--bind"
        }
        self.assertEqual(writable, {str(tree.path.resolve()), str(plan.home)})

    def test_project_b_is_not_bound_at_all(self):
        self.assertTrue((self.repo_b / "PROJECT_B.txt").is_file())
        _, plan = self.plan()
        for bound in plan.bound_host_paths():
            self.assertNotIn(str(self.repo_b.resolve()), bound)

    def test_the_operators_home_is_not_bound(self):
        _, plan = self.plan()
        for bound in plan.bound_host_paths():
            self.assertNotEqual(bound, os.path.expanduser("~"))
            self.assertFalse(bound.startswith(os.path.expanduser("~") + "/cofferdam"))

    def test_the_never_bind_list_is_honoured(self):
        _, plan = self.plan()
        for forbidden in sandbox.NEVER_BIND:
            for bound in plan.bound_host_paths():
                self.assertFalse(
                    bound == forbidden or bound.startswith(forbidden.rstrip("/") + "/"),
                    f"{bound} exposes {forbidden}",
                )

    def test_a_plan_that_would_expose_a_forbidden_path_is_refused(self):
        """The check runs on every launch, not only in this test."""
        forbidden = Path(sandbox.NEVER_BIND[0])
        if not forbidden.exists():  # pragma: no cover - host dependent
            self.skipTest("that path does not exist on this host")
        home = sandbox.build_home(self.dir / "home")
        with self.assertRaises(sandbox.SandboxUnavailable):
            sandbox.build_plan(
                worktree=forbidden, home=home, cli_directory=Path("/usr"),
                command=("/bin/true",),
            )

    def test_the_synthetic_home_holds_no_other_project_history(self):
        """The real ~/.claude.json records every project this machine has seen."""
        home = sandbox.build_home(self.dir / "home")
        state = (home / ".claude.json").read_text()
        self.assertEqual(state.strip(), "{}")
        self.assertNotIn("cofferdam", state)

    def test_the_environment_is_built_by_selection(self):
        _, plan = self.plan()
        self.assertEqual(plan.environment["HOME"], sandbox.INTERIOR_HOME)
        for leaked in ("ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"):
            self.assertNotIn(leaked, plan.environment)

    def test_the_interior_path_hides_the_host_layout(self):
        _, plan = self.plan()
        self.assertEqual(plan.interior_worktree, "/work")

    def test_the_plan_takes_no_caller_supplied_bind(self):
        parameters = set(inspect.signature(sandbox.build_plan).parameters)
        self.assertEqual(
            parameters, {"worktree", "home", "cli_directory", "command"}
        )
        for forbidden in ("binds", "extra_binds", "ro_binds", "policy", "profile"):
            self.assertNotIn(forbidden, parameters)


@unittest.skipUnless(sandbox.available()[0], "bubblewrap is not installed")
class SandboxActuallyContains(ContainmentHarness):
    """Runs a real contained process. Asserts absence *after* asserting presence."""

    def run_contained(self, script: str) -> str:
        tree = self.cut(self.repo_a, "alpha")
        home = sandbox.build_home(self.dir / "home")
        plan = sandbox.build_plan(
            worktree=tree.path, home=home, cli_directory=Path("/usr"),
            command=("/bin/sh", "-c", script),
        )
        result = subprocess.run(
            list(plan.argv), capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin"},
        )
        return (result.stdout or "") + (result.stderr or "")

    def test_the_authorized_worktree_is_readable(self):
        self.assertIn("PROJECT_A.txt", self.run_contained("ls /work"))

    def test_the_worktree_is_writable(self):
        output = self.run_contained("echo written > /work/proof.txt && cat /work/proof.txt")
        self.assertIn("written", output)

    def test_project_b_exists_on_the_host_but_not_inside(self):
        marker = self.repo_b / "PROJECT_B.txt"
        self.assertTrue(marker.is_file(), "the fixture must exist for this to mean anything")
        output = self.run_contained(f"cat {marker} 2>&1 || echo ABSENT")
        self.assertNotIn("belongs to PROJECT_B", output)
        self.assertIn("ABSENT", output)

    def test_the_host_home_directory_does_not_exist_inside(self):
        self.assertTrue(Path(os.path.expanduser("~")).is_dir())
        output = self.run_contained("ls /home 2>&1 || echo NO_HOME")
        self.assertNotIn("nrgis", output)

    def test_the_operators_cli_state_is_not_reachable(self):
        real = Path(os.path.expanduser("~/.claude.json"))
        if not real.is_file():  # pragma: no cover - host dependent
            self.skipTest("no CLI state on this host")
        output = self.run_contained(f"cat {real} 2>&1 || echo ABSENT")
        self.assertIn("ABSENT", output)

    def test_the_canonical_cofferdam_state_is_not_reachable(self):
        output = self.run_contained("ls /home/nrgis/cofferdam 2>&1 || echo ABSENT")
        self.assertIn("ABSENT", output)


# -- the adapter's declared boundary -------------------------------------------


class WorkerAdapterBoundary(ContainmentHarness):
    def adapter(self) -> ClaudeCodeWorkerAdapter:
        return ClaudeCodeWorkerAdapter(state_dir=self.state)

    def test_it_is_a_separate_adapter_from_claude_code(self):
        from cofferdam.workstation.tasks.adapters import CLAUDE_CODE_ADAPTER_ID

        self.assertEqual(ADAPTER_ID, "claude-code-worker")
        self.assertNotEqual(ADAPTER_ID, CLAUDE_CODE_ADAPTER_ID)

    def test_the_existing_claude_code_profile_still_has_no_bash(self):
        """The boundary this PR must not widen."""
        from cofferdam.workstation.tasks.adapters.claude_code import cli as plain

        self.assertNotIn("Bash", plain.PROFILE_TOOLS)
        self.assertEqual(plain.PROFILE_TOOLS, ("Read", "Write", "Edit", "Glob", "Grep"))

    def test_it_is_off_by_default(self):
        self.assertEqual(build_registry().ids(), ())
        self.assertNotIn(
            ADAPTER_ID,
            build_registry(enable_claude_code_adapter=True).ids(),
        )

    def test_the_registry_still_takes_booleans_and_no_location(self):
        """Adding a worker did not put a path on the code-owned adapter table.

        The obvious shape was ``build_registry(..., worker_state_dir=...)``, and
        it would have been the first *location* any adapter switch carried. The
        adapter resolves its own directory from host configuration instead.
        """
        parameters = inspect.signature(build_registry).parameters
        self.assertIn("enable_claude_code_worker_adapter", parameters)
        for parameter in parameters.values():
            self.assertIsInstance(parameter.default, bool)
        for forbidden in ("worker_state_dir", "state_dir", "path", "root", "config"):
            self.assertNotIn(forbidden, parameters)

    def test_it_registers_under_its_own_id(self):
        registry = build_registry(enable_claude_code_worker_adapter=True)
        self.assertEqual(registry.ids(), (ADAPTER_ID,))

    def test_the_default_state_directory_is_host_owned(self):
        """Resolved from COFFERDAM_HOME / ~/cofferdam, never from a request."""
        resolved = worktree.default_state_dir()
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "state")

    def test_it_declares_no_followup_and_no_approvals(self):
        capabilities = self.adapter().capabilities()
        self.assertTrue(capabilities.start)
        self.assertTrue(capabilities.cancel)
        self.assertFalse(capabilities.followup)
        self.assertFalse(capabilities.approvals)
        self.assertFalse(capabilities.clarifications)

    def test_the_interior_argv_carries_no_forbidden_flag(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        for forbidden in cli.FORBIDDEN_FLAGS:
            self.assertNotIn(forbidden, argv, f"the worker profile uses {forbidden}")

    def test_mcp_is_disabled_and_no_settings_file_is_read(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("--mcp-config", argv)
        # `--setting-sources ""` means the profile in source is the profile that
        # runs: a `.claude/settings.json` in the worktree cannot widen it.
        index = argv.index("--setting-sources")
        self.assertEqual(argv[index + 1], "")

    def test_the_prompt_is_never_an_argument(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        parameters = set(inspect.signature(cli.build_interior_argv).parameters)
        self.assertEqual(parameters, {"interior_cli", "interior_worktree"})
        self.assertIn("-p", argv)
        self.assertNotIn("--prompt", argv)

    def test_no_command_tool_is_granted_at_all(self):
        """Replaces an earlier denylist test. There are no commands to deny."""
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        granted = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
        self.assertNotIn("Bash", granted)
        self.assertFalse(any(tool.startswith("Bash") for tool in granted))

    def test_the_worker_cannot_merge_or_deploy_because_it_cannot_run_anything(self):
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        granted = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
        self.assertEqual(
            set(granted),
            {
                "Read(/work/**)",
                "Write(/work/**)",
                "Edit(/work/**)",
                "Glob(/work/**)",
                "Grep(/work/**)",
            },
        )

    def test_every_granted_tool_is_scoped_to_the_worktree(self):
        """A grant that names no path is a grant over everything mounted."""
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/work"
        )
        granted = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
        self.assertTrue(granted)
        for tool in granted:
            self.assertTrue(
                tool.endswith("(/work/**)"),
                f"{tool} is granted without a path scope",
            )

    def test_the_scope_follows_the_interior_worktree_constant(self):
        """The scope is derived, so it cannot point at a path that moved."""
        argv = cli.build_interior_argv(
            interior_cli="/opt/claude-cli", interior_worktree="/elsewhere"
        )
        granted = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
        self.assertIn("Read(/elsewhere/**)", granted)
        self.assertNotIn("Read(/work/**)", granted)

    def test_the_worker_commits_under_its_own_identity(self):
        self.assertEqual(cli.GIT_ENVIRONMENT["GIT_AUTHOR_NAME"], "Cofferdam Worker")
        self.assertNotIn("nrgis", repr(cli.GIT_ENVIRONMENT))

    def test_the_execution_contract_is_separable_from_the_prompt(self):
        tree = self.cut(self.repo_a, "alpha")
        approved = "Do the approved thing.\nExactly this.\n"
        payload = build_worker_payload(approved, tree)
        self.assertEqual(delivered_prompt(payload), approved)
        self.assertIn(PROMPT_SEPARATOR, payload)

    def test_the_contract_names_the_interior_path_not_the_host_one(self):
        tree = self.cut(self.repo_a, "alpha")
        payload = build_worker_payload("do it", tree)
        self.assertIn("/work", payload)
        self.assertNotIn(str(tree.path), payload)

    def test_it_refuses_rather_than_running_uncontained(self):
        """No fallback. A host without containment starts no process at all.

        Behavioural, not a source scan: containment is made to report itself
        unavailable, ``subprocess.Popen`` is made to explode, and the refusal has
        to arrive without the explosion — which is only possible if nothing was
        launched.
        """
        import cofferdam.workstation.tasks.adapters.claude_code_worker.adapter as module
        from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal

        original_available = sandbox.available
        original_popen = subprocess.Popen

        sandbox.available = lambda: (False, "bubblewrap is not installed")
        module.subprocess.Popen = _no_worker(original_popen)
        self.addCleanup(setattr, module.subprocess, "Popen", original_popen)
        self.addCleanup(setattr, sandbox, "available", original_available)

        adapter = self.adapter()
        self.assertFalse(adapter.available())
        with self.assertRaises(AdapterRefusal):
            adapter.start(self.context())

    def test_a_worktree_that_cannot_be_cut_starts_nothing(self):
        import cofferdam.workstation.tasks.adapters.claude_code_worker.adapter as module
        from cofferdam.workstation.tasks.adapters.protocol import AdapterRefusal

        original_popen = module.subprocess.Popen
        module.subprocess.Popen = _no_worker(original_popen)
        self.addCleanup(setattr, module.subprocess, "Popen", original_popen)

        plain = self.dir / "not-a-repo"
        plain.mkdir()
        with self.assertRaises(AdapterRefusal):
            self.adapter().start(self.context(project_root=plain))

    def context(self, *, project_root=None):
        from cofferdam.workstation.tasks.adapters.protocol import TaskContext

        return TaskContext(
            task_id=new_task_id(),
            correlation_id="tcor-0000000000000000",
            project_id="alpha",
            project_root=Path(project_root or self.repo_a.resolve()),
            adapter_id=ADAPTER_ID,
            prompt="do the approved thing",
            state="created",
            lifecycle_revision=0,
        )

    def test_it_exposes_no_dispatch_or_replan_operation(self):
        for forbidden in ("dispatch", "replan", "plan", "merge", "deploy", "push_to_main"):
            self.assertFalse(hasattr(ClaudeCodeWorkerAdapter, forbidden))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
