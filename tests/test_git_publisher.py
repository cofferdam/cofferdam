"""Publishing a worker branch: the gates, the secret boundary, the idempotency.

Three things this file is built to prove
-----------------------------------------

**The publishing credential is a third authority.** Not the model's, not the
operator's. A fake token is planted, proved present, and then proved absent from
the worker namespace, the check namespace, the worker environment, the task
result and every read model — the same plant-then-exclude discipline the
credential-isolation suite uses, because a test that only asserts an absence
passes just as happily against an empty fixture.

**Nothing about where it publishes comes from a caller.** ``publish`` takes one
dispatch id. Repository, branch, base and commit are all derived, and the tests
below try to substitute each of them.

**GitHub is external state, so both writes are re-entrant.** The push tests run
against a **real local bare repository** — real Git, real refs, real
fast-forward rules — rather than a mock, so "no force push" and "a second push
is a no-op" are properties of the operation and not of a stub. The GitHub API
half is faked at the transport, because there is no honest way to test PR
creation without a repository nobody has authorized creating.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from cofferdam.workstation.publisher import credential, github, remote, service
from cofferdam.workstation.publisher.service import GitPublisher
from cofferdam.workstation.worker import sandbox, session, worktree

FAKE_TOKEN = "github_pat_11FAKESENTINEL_DoNotExfiltrate0123456789abcdef"
WORKER_EMAIL = "worker@cofferdam.local"


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


class PublisherHarness(unittest.TestCase):
    """A real repository, a real bare remote, and a fake publisher token."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state_dir = self.dir / "state"
        self.state_dir.mkdir()

        # A real bare repository standing in for GitHub's Git side. Real refs and
        # real fast-forward rules; only the REST API is faked.
        self.bare = self.dir / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)],
                       check=True)

        self.repo = self.dir / "project"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        git(self.repo, "remote", "add", "origin", "https://github.com/acme/widget.git")
        git(self.repo, "push", "-q", str(self.bare), "main")

        self.task_id = "task_" + "a" * 26
        self.branch = worktree.branch_name(self.task_id)

    def plant_token(self) -> Path:
        credential.store(self.state_dir, FAKE_TOKEN)
        return credential.credentials_file(self.state_dir)

    def cut_worktree(self, task_id=None) -> worktree.DevelopmentWorktree:
        tree = worktree.prepare(
            project_id="alpha", project_root=self.repo,
            task_id=task_id or self.task_id, state_dir=self.state_dir,
        )
        return tree

    def worker_commit(self, tree) -> str:
        (tree.path / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
        )
        return worktree.commit_all(
            tree, message="worker: approved development step",
            author="Cofferdam Worker", email=WORKER_EMAIL,
        )

    def bare_repository(self) -> remote.GitHubRepository:
        """A repository object whose push URL is the local bare repo.

        Real Git, real refs. Only the *address* is local.
        """
        found = remote.GitHubRepository(owner="acme", repo="widget")
        return mock.patch.object(
            type(found), "https_url",
            property(lambda self, path=str(self.bare) if False else str(self.bare): path),
        ), found


# -- the credential is its own authority --------------------------------------


class TheTokenIsSeparateFromEveryOtherCredential(PublisherHarness):
    def test_the_store_is_not_inside_the_claude_session(self):
        publisher = credential.publisher_root(self.state_dir)
        claude = session.config_directory(self.state_dir)
        self.assertNotEqual(publisher, claude)
        self.assertFalse(str(publisher).startswith(str(claude) + os.sep))
        self.assertFalse(str(claude).startswith(str(publisher) + os.sep))

    def test_the_credential_is_owner_only_from_the_moment_it_exists(self):
        path = self.plant_token()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            credential.publisher_root(self.state_dir).stat().st_mode & 0o777, 0o700
        )

    def test_a_fresh_publisher_is_unconfigured_not_ready(self):
        credential.prepare(self.state_dir)
        found = credential.status(self.state_dir)
        self.assertEqual(found.status, credential.STATUS_UNCONFIGURED)
        self.assertFalse(found.usable)
        self.assertTrue(found.needs_configuration)

    def test_nothing_reads_the_operator_gh_credential(self):
        """No import path, no fallback, no `gh` invocation anywhere."""
        import ast

        for module in (credential, github, remote, service):
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    if (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    ):
                        node.body = node.body[1:]
            code = ast.unparse(tree)
            for forbidden in ("hosts.yml", "GH_TOKEN", "GITHUB_TOKEN", "keyring"):
                self.assertNotIn(forbidden, code, f"{module.__name__}: {forbidden}")
            # `gh` must not be executed. The word appears in prose; this checks
            # the code, and specifically anything that could become argv.
            self.assertNotIn('"gh"', code, module.__name__)
            self.assertNotIn("'gh'", code, module.__name__)

    def test_an_unsafe_mode_is_refused(self):
        path = self.plant_token()
        os.chmod(path, 0o644)
        found = credential.status(self.state_dir)
        self.assertEqual(found.status, credential.STATUS_PERMISSIONS_UNSAFE)
        with self.assertRaises(credential.PublisherCredentialUnavailable):
            credential.require_usable(self.state_dir)

    def test_require_usable_returns_a_path_never_the_token(self):
        self.plant_token()
        returned = credential.require_usable(self.state_dir)
        self.assertIsInstance(returned, Path)
        self.assertNotIn(FAKE_TOKEN, str(returned))

    def test_a_classic_token_is_stored_but_named_as_classic(self):
        credential.store(self.state_dir, "ghp_" + "b" * 36)
        self.assertEqual(credential.status(self.state_dir).token_kind, "classic")
        credential.store(self.state_dir, FAKE_TOKEN)
        self.assertEqual(credential.status(self.state_dir).token_kind, "fine_grained")

    def test_a_value_that_would_corrupt_the_file_is_refused(self):
        for bad in ("", "tok\nen", "tok@en", "tok/en"):
            with self.subTest(value=bad):
                with self.assertRaises(credential.PublisherCredentialUnavailable):
                    credential.store(self.state_dir, bad)


# -- the fake token reaches no worker surface ---------------------------------


class TheWorkerNeverSeesThePublisherToken(PublisherHarness):
    """Plant it, prove it is there, then prove every boundary excludes it."""

    def setUp(self):
        super().setUp()
        self.token_path = self.plant_token()

    def test_the_fixture_is_real(self):
        """If this fails, every exclusion below is meaningless."""
        self.assertTrue(self.token_path.is_file())
        self.assertIn(FAKE_TOKEN, self.token_path.read_text())

    def test_the_claude_namespace_does_not_bind_the_publisher_directory(self):
        config = session.prepare(self.state_dir)
        work = self.dir / "work"
        work.mkdir()
        with mock.patch.object(sandbox, "find_bwrap", return_value=Path("/usr/bin/bwrap")):
            plan = sandbox.build_plan(
                worktree=work, cli_directory=Path("/usr"),
                command=("/bin/true",), session_config=config,
            )
        publisher = str(credential.publisher_root(self.state_dir))
        for bound in plan.bound_host_paths():
            self.assertFalse(
                bound == publisher or bound.startswith(publisher + os.sep),
                f"{bound} exposes the publishing credential",
            )
        rendered = " ".join(plan.argv) + json.dumps(plan.environment)
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertNotIn(publisher, rendered)

    @unittest.skipUnless(sandbox.available()[0], "bubblewrap is not installed")
    def test_the_token_is_absent_from_the_worker_namespace_when_it_runs(self):
        """Non-vacuous: a real contained process looks for it and cannot find it."""
        config = session.prepare(self.state_dir)
        work = self.dir / "work"
        work.mkdir()
        plan = sandbox.build_plan(
            worktree=work, cli_directory=Path("/usr"),
            command=(
                "/bin/sh", "-c",
                "cat " + str(self.token_path) + " 2>&1; "
                "ls -la " + str(credential.publisher_root(self.state_dir)) + " 2>&1",
            ),
            session_config=config,
        )
        result = subprocess.run(list(plan.argv), capture_output=True, text=True,
                                timeout=60, env={"PATH": "/usr/bin:/bin"})
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn(FAKE_TOKEN, output, "the worker namespace reached the token")
        self.assertIn("No such file", output)

    @unittest.skipUnless(sandbox.available()[0], "bubblewrap is not installed")
    def test_the_token_is_absent_from_the_check_namespace(self):
        from cofferdam.workstation.worker import checks

        work = self.dir / "work"
        work.mkdir()
        (work / "look.py").write_text(
            "import os\n"
            f"for p in ({str(self.token_path)!r},):\n"
            "    try:\n        print('LEAKED', open(p).read())\n"
            "    except Exception as exc:\n        print('DENIED', type(exc).__name__)\n"
        )
        argv = checks.build_plan(worktree=work, command=("python3", "look.py"))
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                env={"PATH": "/usr/bin:/bin"})
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn(FAKE_TOKEN, output)
        self.assertIn("DENIED", output)

    def test_the_check_plan_names_no_publisher_path(self):
        from cofferdam.workstation.worker import checks

        work = self.dir / "work"
        work.mkdir()
        rendered = " ".join(checks.build_plan(worktree=work, command=("true",)))
        self.assertNotIn(str(credential.publisher_root(self.state_dir)), rendered)
        self.assertNotIn("git-publisher", rendered)

    def test_the_publication_read_model_carries_no_token_and_no_path(self):
        from cofferdam.workstation.planner.store import Publication

        row = Publication(
            publication_id="pub_1", dispatch_id="d", planner_request_id="p",
            task_id=self.task_id, project_id="alpha", workspace_id=None,
            repository="acme/widget", branch=self.branch, base_branch="main",
            commit_sha="a" * 40, state="published", actor="cofferdam",
            source="publisher", created_at="t", updated_at="t",
            pull_request_number=7, pull_request_url="https://github.com/acme/widget/pull/7",
        )
        rendered = json.dumps(row.to_dict())
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertNotIn(str(self.dir), rendered)
        self.assertNotIn("git-publisher", rendered)
        self.assertNotIn("credential", rendered)

    def test_the_doctor_never_prints_the_token(self):
        payload = credential.describe(self.state_dir)
        rendered = json.dumps(payload)
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertNotIn(str(self.token_path), rendered)
        self.assertEqual(payload["token_kind"], "fine_grained")

    def test_git_chatter_is_scrubbed_of_credential_shapes(self):
        noisy = (
            "fatal: unable to access 'https://x-access-token:"
            + FAKE_TOKEN + "@github.com/acme/widget.git/'"
        )
        scrubbed = github._scrub(noisy)
        self.assertNotIn(FAKE_TOKEN, scrubbed)
        self.assertIn("[redacted]", scrubbed)


# -- branch policy -------------------------------------------------------------


class OnlyAWorkerBranchIsPublishable(unittest.TestCase):
    def test_the_real_worker_branch_is_accepted(self):
        branch = worktree.branch_name("task_" + "c" * 26)
        self.assertEqual(github.publishable_branch(branch), branch)

    def test_protected_branches_are_refused(self):
        for name in github.PROTECTED_BRANCHES:
            with self.subTest(branch=name):
                with self.assertRaises(github.PublishRefused) as caught:
                    github.publishable_branch(name)
                self.assertIn(caught.exception.reason,
                              ("branch_protected", "branch_not_worker_owned"))

    def test_a_branch_outside_the_worker_prefix_is_refused(self):
        for name in ("feature/x", "release/2.0", "cofferdam/other/x", "hotfix"):
            with self.subTest(branch=name):
                with self.assertRaises(github.PublishRefused):
                    github.publishable_branch(name)

    def test_a_branch_that_could_be_read_as_a_flag_is_refused(self):
        for name in ("--force", "-u", "--upload-pack=/bin/sh", ""):
            with self.subTest(branch=name):
                with self.assertRaises(github.PublishRefused):
                    github.publishable_branch(name)

    def test_a_malformed_ref_is_refused(self):
        for name in (
            "cofferdam/worker/../../main",
            "cofferdam/worker/x.lock",
            "cofferdam/worker/x/",
            "cofferdam/worker/a b",
        ):
            with self.subTest(branch=name):
                with self.assertRaises(github.PublishRefused):
                    github.publishable_branch(name)

    def test_the_prefix_matches_what_the_worker_actually_produces(self):
        """If these drifted, every worker branch would become unpublishable."""
        self.assertTrue(
            worktree.branch_name("task_" + "d" * 26).startswith(
                github.WORKER_BRANCH_PREFIX
            )
        )


# -- the push, against a real Git remote ---------------------------------------


class ThePushIsFastForwardOnly(PublisherHarness):
    """Real refs, real fast-forward rules. Not a mock."""

    def setUp(self):
        super().setUp()
        self.credentials_path = self.plant_token()
        self.tree = self.cut_worktree()
        self.commit = self.worker_commit(self.tree)
        self.repository = remote.GitHubRepository(owner="acme", repo="widget")

    def push(self, branch=None, commit=None):
        with mock.patch.object(
            remote.GitHubRepository, "https_url",
            property(lambda _self: str(self.bare)),
        ):
            return github.push(
                worktree=self.tree.path, repository=self.repository,
                branch=branch or self.branch, expected_commit=commit or self.commit,
                credentials_path=self.credentials_path,
            )

    def remote_head(self, branch=None):
        name = branch or self.branch
        out = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{name}"],
            cwd=self.bare, capture_output=True, text=True,
        )
        return (out.stdout or "").strip() if out.returncode == 0 else None

    def test_the_branch_lands_at_the_exact_commit(self):
        state, _ = self.push()
        self.assertEqual(state, github.PUSH_CREATED)
        self.assertEqual(self.remote_head(), self.commit)

    def test_pushing_twice_is_a_no_op(self):
        self.push()
        state, detail = self.push()
        self.assertEqual(state, github.PUSH_ALREADY_CURRENT, detail)
        self.assertEqual(self.remote_head(), self.commit)

    def test_main_is_untouched_by_a_worker_push(self):
        before = subprocess.run(["git", "rev-parse", "refs/heads/main"],
                                cwd=self.bare, capture_output=True, text=True).stdout
        self.push()
        after = subprocess.run(["git", "rev-parse", "refs/heads/main"],
                               cwd=self.bare, capture_output=True, text=True).stdout
        self.assertEqual(after, before)

    def test_a_diverged_remote_is_refused_rather_than_forced(self):
        """The property that makes 'no force push' real rather than a comment."""
        self.push()
        # Somebody else advances the remote branch.
        other = self.dir / "other"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        git(other, "checkout", "-q", self.branch)
        (other / "theirs.txt").write_text("someone else's work\n")
        git(other, "add", "-A")
        git(other, "commit", "-qm", "theirs")
        git(other, "push", "-q", "origin", self.branch)
        theirs = self.remote_head()

        # Cofferdam now has a different commit on the same branch.
        (self.tree.path / "calc.py").write_text("def add(a, b):\n    return a + b\n# more\n")
        worktree.commit_all(self.tree, message="worker: second",
                            author="Cofferdam Worker", email=WORKER_EMAIL)
        with self.assertRaises(github.PublishRefused) as caught:
            self.push(commit="ignored")
        self.assertEqual(caught.exception.reason, "remote_diverged")
        self.assertEqual(self.remote_head(), theirs, "the remote was overwritten")

    def test_the_refspec_is_exact_and_carries_no_force(self):
        recorded = {}

        def fake(root, arguments):
            recorded["argv"] = list(arguments)
            return subprocess.CompletedProcess([], 0, "Everything up-to-date", "")

        with mock.patch.object(github, "_git", fake):
            self.push()
        argv = recorded["argv"]
        self.assertIn(f"refs/heads/{self.branch}:refs/heads/{self.branch}", argv)
        for forbidden in (
            "--force", "-f", "--force-with-lease", "--delete", "--tags",
            "--mirror", "--all", "--set-upstream", "-u",
        ):
            self.assertNotIn(forbidden, argv, forbidden)
        self.assertNotIn(FAKE_TOKEN, " ".join(argv), "the token reached argv")

    def test_the_token_never_appears_in_the_push_argv_or_url(self):
        recorded = {}

        def fake(root, arguments):
            recorded["argv"] = list(arguments)
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch.object(github, "_git", fake):
            self.push()
        rendered = " ".join(recorded["argv"])
        self.assertNotIn(FAKE_TOKEN, rendered)
        self.assertIn("credential.helper=store --file=", rendered)

    def test_remote_branch_commit_reads_without_writing(self):
        self.assertIsNone(self._ls())
        self.push()
        self.assertEqual(self._ls(), self.commit)

    def _ls(self):
        with mock.patch.object(
            remote.GitHubRepository, "https_url",
            property(lambda _self: str(self.bare)),
        ):
            return github.remote_branch_commit(
                worktree=self.tree.path, repository=self.repository,
                branch=self.branch, credentials_path=self.credentials_path,
            )


# -- the pull request ----------------------------------------------------------


class FakeGitHub:
    """A transport-level stand-in. Records calls, answers like GitHub."""

    def __init__(self):
        self.calls = []
        self.pulls = []
        self.next_number = 41
        self.fail_with = None

    def __call__(self, state_dir, method, path, *, body=None, timeout=30.0):
        self.calls.append((method, path, body))
        if self.fail_with is not None:
            return self.fail_with
        if method == "GET" and "/pulls?" in path:
            head = path.split("head=")[1].split("&")[0]
            branch = head.split(":", 1)[1]
            return 200, [p for p in self.pulls if p["head"]["ref"] == branch]
        if method == "POST" and path.endswith("/pulls"):
            for existing in self.pulls:
                if existing["head"]["ref"] == body["head"]:
                    return 422, {"message": "A pull request already exists"}
            self.next_number += 1
            created = {
                "number": self.next_number,
                "html_url": f"https://github.com/acme/widget/pull/{self.next_number}",
                "state": "open",
                "merged_at": None,
                "head": {"ref": body["head"]},
                "base": {"ref": body["base"]},
            }
            self.pulls.append(created)
            return 201, created
        return 404, {"message": "not found"}


class ThePullRequestIsCreatedOnce(PublisherHarness):
    def setUp(self):
        super().setUp()
        self.plant_token()
        self.api = FakeGitHub()
        patch = mock.patch.object(credential, "api_request", self.api)
        patch.start()
        self.addCleanup(patch.stop)
        self.repository = remote.GitHubRepository(owner="acme", repo="widget")

    def create(self):
        return github.create_pull_request(
            self.state_dir, self.repository, branch=self.branch, base="main",
            title="t", body="b",
        )

    def test_a_pull_request_is_created(self):
        found = self.create()
        self.assertEqual(found["number"], 42)
        self.assertTrue(found["url"].endswith("/pull/42"))
        self.assertEqual(found["base"], "main")

    def test_creating_twice_returns_the_same_pull_request(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first["number"], second["number"])
        self.assertEqual(len(self.api.pulls), 1, "a duplicate PR was created")

    def test_an_existing_pull_request_is_found_by_exact_head(self):
        self.create()
        found = github.find_pull_request(
            self.state_dir, self.repository, branch=self.branch, base="main"
        )
        self.assertEqual(found["number"], 42)
        other = github.find_pull_request(
            self.state_dir, self.repository,
            branch=worktree.branch_name("task_" + "z" * 26), base="main",
        )
        self.assertIsNone(other, "a different branch matched somebody else's PR")

    def test_the_request_body_has_exactly_four_fields(self):
        self.create()
        _, _, body = self.api.calls[-1]
        self.assertEqual(set(body), {"title", "body", "head", "base"})
        for forbidden in ("merge_method", "draft", "reviewers", "labels", "assignees"):
            self.assertNotIn(forbidden, body)

    def test_nothing_here_can_merge(self):
        self.create()
        for method, path, _ in self.api.calls:
            self.assertNotIn("/merge", path)
            self.assertNotEqual(method, "PUT")

    def test_an_auth_failure_is_classified_as_publisher_auth_required(self):
        self.api.fail_with = (403, {"message": "Resource not accessible"})
        with self.assertRaises(github.PublishRefused) as caught:
            self.create()
        self.assertEqual(caught.exception.reason, "publisher_auth_required")

    def test_an_unexpected_status_is_a_truthful_failure(self):
        self.api.fail_with = (500, {"message": "boom"})
        with self.assertRaises(github.PublishRefused) as caught:
            self.create()
        self.assertEqual(caught.exception.reason, "pull_request_failed")


# -- the remote identity chain -------------------------------------------------


class TheRepositoryComesFromTheProject(PublisherHarness):
    def test_it_is_read_from_the_checkout(self):
        found = remote.resolve(self.repo)
        self.assertEqual(found.full_name, "acme/widget")

    def test_a_project_with_no_remote_is_refused(self):
        bare = self.dir / "noremote"
        bare.mkdir()
        git(bare, "init", "-q", "-b", "main")
        with self.assertRaises(remote.RemoteUnresolved):
            remote.resolve(bare)

    def test_project_b_cannot_be_reached_through_project_a(self):
        """Two projects, two remotes; resolving A never yields B's."""
        other = self.dir / "beta"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "remote", "add", "origin", "https://github.com/acme/other.git")
        self.assertEqual(remote.resolve(self.repo).full_name, "acme/widget")
        self.assertEqual(remote.resolve(other).full_name, "acme/other")

    def test_a_changed_remote_changes_the_answer(self):
        """Fail-closed on substitution: the identity is re-read, never cached."""
        git(self.repo, "remote", "set-url", "origin", "https://gitlab.com/acme/widget")
        with self.assertRaises(remote.RemoteUnresolved):
            remote.resolve(self.repo)

    def test_no_publisher_entry_point_takes_a_repository(self):
        import inspect

        self.assertEqual(
            set(inspect.signature(GitPublisher.publish).parameters),
            {"self", "dispatch_id"},
        )
        constructor = set(inspect.signature(GitPublisher.__init__).parameters)
        for forbidden in ("repository", "remote", "url", "owner", "repo", "branch",
                          "commit", "token", "credential"):
            self.assertNotIn(forbidden, constructor)

    def test_the_push_url_never_carries_a_credential(self):
        url = remote.GitHubRepository(owner="acme", repo="widget").https_url
        self.assertEqual(url, "https://github.com/acme/widget.git")
        self.assertNotIn("@", url)


# -- schema --------------------------------------------------------------------


class ThePublicationSchemaIsAdditive(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_v4_database_migrates_and_keeps_its_rows(self):
        import sqlite3

        from cofferdam.workstation.planner import new_planner_request_id
        from cofferdam.workstation.planner.store import (
            PLANNER_SCHEMA_VERSION,
            PlannerStore,
        )

        store = PlannerStore(self.dir / "planner")
        request_id = new_planner_request_id()
        store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id="alpha",
            user_intent="devam", request_payload={}, projection_policy_id="p",
            projection_built_at="2026-08-21T00:00:00Z",
            created_at="2026-08-21T00:00:00Z",
        )
        path = self.dir / "planner" / "planner.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE planner_publications")
            connection.execute(
                "UPDATE schema_meta SET value = '4' WHERE key = 'schema_version'"
            )
        reopened = PlannerStore(self.dir / "planner")
        self.assertEqual(reopened.get(request_id).user_intent, "devam")
        self.assertEqual(reopened.schema_version(), PLANNER_SCHEMA_VERSION)
        with sqlite3.connect(path) as connection:
            connection.execute("SELECT * FROM planner_publications")

    def test_a_future_database_is_refused(self):
        import sqlite3

        from cofferdam.workstation.planner.store import (
            PlannerStore,
            PlannerStoreUnavailable,
        )

        PlannerStore(self.dir / "planner")
        with sqlite3.connect(self.dir / "planner" / "planner.sqlite3") as connection:
            connection.execute(
                "UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'"
            )
        with self.assertRaises(PlannerStoreUnavailable):
            PlannerStore(self.dir / "planner")

    def _store_with_dispatch(self):
        """A store holding a real planner request and a real dispatch.

        Built properly rather than with placeholder ids because
        `planner_publications` has a foreign key onto the dispatch — a
        publication may not exist without the thing that authorized it, which
        `test_a_publication_requires_its_dispatch` pins directly.
        """
        from cofferdam.workstation.planner import (
            ACTION_PREPARE_WORKER_PROMPT,
            PlannerResult,
            PlannerStore,
            ProviderExecution,
            new_planner_request_id,
        )
        from cofferdam.workstation.planner.store import WorkerDispatch

        store = PlannerStore(self.dir / "planner")
        request_id = new_planner_request_id()
        store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id="alpha",
            user_intent="devam", request_payload={}, projection_policy_id="p",
            projection_built_at="t", created_at="t",
        )
        store.mark_running(request_id, started_at="t")
        store.record_success(
            request_id,
            result=PlannerResult(
                action=ACTION_PREPARE_WORKER_PROMPT, summary="s", confidence=0.9,
                worker_prompt="do it", decision_basis="b",
            ),
            execution=ProviderExecution(provider_id="claude_code"),
            completed_at="t",
        )
        dispatch = WorkerDispatch(
            dispatch_id="dsp_1", planner_request_id=request_id,
            authority_event_id="auth_1", subject_fingerprint="f" * 64,
            worker_prompt_sha256="s" * 64, project_id="alpha", workspace_id=None,
            adapter_id="claude-code-worker", task_id="task_" + "a" * 26,
            request_key="k", branch="cofferdam/worker/task_" + "a" * 26,
            actor="user", source="internal_test", created_at="t",
        )
        store.record_dispatch(dispatch)
        return store, dispatch

    def test_a_publication_requires_its_dispatch(self):
        """A publication asserts a real relationship. It cannot dangle."""
        import sqlite3

        from cofferdam.workstation.planner.store import Publication, PlannerStore

        store = PlannerStore(self.dir / "planner")
        with self.assertRaises(sqlite3.IntegrityError):
            store.upsert_publication(
                Publication(
                    publication_id="pub_x", dispatch_id="nonexistent",
                    planner_request_id="p", task_id="t", project_id="alpha",
                    workspace_id=None, repository="acme/widget",
                    branch="cofferdam/worker/t", base_branch="main",
                    commit_sha="a" * 40, state="pending", actor="cofferdam",
                    source="publisher", created_at="t", updated_at="t",
                )
            )

    def test_one_publication_per_dispatch(self):
        from cofferdam.workstation.planner.store import Publication

        store, dispatch = self._store_with_dispatch()
        row = Publication(
            publication_id="pub_1", dispatch_id=dispatch.dispatch_id,
            planner_request_id=dispatch.planner_request_id,
            task_id=dispatch.task_id, project_id="alpha", workspace_id=None,
            repository="acme/widget", branch=dispatch.branch,
            base_branch="main", commit_sha="a" * 40, state="pending",
            actor="cofferdam", source="publisher", created_at="t", updated_at="t",
        )
        store.upsert_publication(row)
        store.upsert_publication(
            Publication(**{**row.__dict__, "state": "published",
                           "pull_request_number": 7, "updated_at": "t2"})
        )
        found = store.publication(dispatch.dispatch_id)
        self.assertEqual(found.state, "published")
        self.assertEqual(found.pull_request_number, 7)
        self.assertEqual(found.publication_id, "pub_1", "a second row was created")
        self.assertEqual(found.created_at, "t", "created_at was overwritten")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# -- the whole publish, gates and all ------------------------------------------


class ThePublishGates(PublisherHarness):
    """`publish(dispatch_id)` end to end, against a real bare remote.

    The API half is faked; the Git half is not. Every refusal below is checked
    for *what it did not do* as well as what it said — a gate that reports a
    refusal after already pushing would be no gate at all.
    """

    def setUp(self):
        super().setUp()
        self.plant_token()
        self.api = FakeGitHub()
        patch = mock.patch.object(credential, "api_request", self.api)
        patch.start()
        self.addCleanup(patch.stop)
        # Every push in this class goes to the local bare repository.
        url = mock.patch.object(
            remote.GitHubRepository, "https_url",
            property(lambda _self: str(self.bare)),
        )
        url.start()
        self.addCleanup(url.stop)

        self.store, self.dispatch = self._planner_state()
        self.tasks = self._task_double("completed")
        self.tree = self.cut_worktree()
        self.commit = self.worker_commit(self.tree)
        self.publisher = GitPublisher(
            store=self.store, tasks=self.tasks, state_dir=self.state_dir
        )

    def _planner_state(self):
        from cofferdam.workstation.planner import (
            ACTION_PREPARE_WORKER_PROMPT,
            AuthorityProvenance,
            PlannerAuthorityService,
            PlannerResult,
            PlannerStore,
            ProviderExecution,
            new_planner_request_id,
        )
        from cofferdam.workstation.planner.dispatch import (
            WORKER_KIND_CLAUDE_CODE,
            dispatch_request_key,
            new_dispatch_id,
            worker_prompt_digest,
        )
        from cofferdam.workstation.planner.store import WorkerDispatch

        store = PlannerStore(self.dir / "planner")
        request_id = new_planner_request_id()
        store.create_request(
            planner_request_id=request_id, workspace_id=None, project_id="alpha",
            user_intent="devam", request_payload={}, projection_policy_id="p",
            projection_built_at="t", created_at="t",
        )
        store.mark_running(request_id, started_at="t")
        store.record_success(
            request_id,
            result=PlannerResult(
                action=ACTION_PREPARE_WORKER_PROMPT, summary="add sub()",
                confidence=0.9, worker_prompt="Implement sub().\n",
                decision_basis="context was sufficient",
            ),
            execution=ProviderExecution(provider_id="claude_code"),
            completed_at="t",
        )
        authority = PlannerAuthorityService(store=store)
        gate = authority.gate(request_id)
        event = authority.approve_prepared_worker_prompt(
            request_id, expected_subject_fingerprint=gate.subject_fingerprint,
            provenance=AuthorityProvenance.internal_test(),
        )
        dispatch = WorkerDispatch(
            dispatch_id=new_dispatch_id(), planner_request_id=request_id,
            authority_event_id=getattr(event, "authority_event_id", "auth"),
            subject_fingerprint=gate.subject_fingerprint,
            worker_prompt_sha256=worker_prompt_digest("Implement sub().\n"),
            project_id="alpha", workspace_id=None,
            adapter_id="claude-code-worker", task_id=self.task_id,
            request_key=dispatch_request_key(
                planner_request_id=request_id,
                subject_fingerprint=gate.subject_fingerprint,
                worker_kind=WORKER_KIND_CLAUDE_CODE,
            ),
            branch=self.branch, actor="user", source="internal_test",
            created_at="t",
        )
        store.record_dispatch(dispatch)
        return store, dispatch

    def _task_double(self, state):
        from cofferdam.workstation.tasks.projects import ProjectRegistry, TaskProject

        project = TaskProject(
            project_id="alpha", display_name="Alpha", root=self.repo.resolve(),
            adapters=("claude-code-worker",),
        )
        registry = ProjectRegistry(projects=(project,), source_present=True)

        class Tasks:
            projects = registry

            def get_task(self, task_id):
                return type("Row", (), {"state": state, "task_id": task_id})()

        return Tasks()

    def remote_head(self):
        out = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{self.branch}"],
            cwd=self.bare, capture_output=True, text=True,
        )
        return (out.stdout or "").strip() if out.returncode == 0 else None

    # -- the happy path ---------------------------------------------------

    def test_a_finished_dispatch_publishes(self):
        view = self.publisher.publish(self.dispatch.dispatch_id)
        payload = view.to_dict()
        self.assertTrue(payload["published"], payload)
        self.assertEqual(payload["commit"], self.commit)
        self.assertEqual(payload["branch"], self.branch)
        self.assertEqual(payload["repository"], "acme/widget")
        self.assertEqual(payload["base_branch"], "main")
        self.assertEqual(payload["pull_request"]["number"], 42)
        self.assertEqual(self.remote_head(), self.commit)

    def test_publishing_twice_is_the_same_branch_and_the_same_pr(self):
        first = self.publisher.publish(self.dispatch.dispatch_id).to_dict()
        second = self.publisher.publish(self.dispatch.dispatch_id).to_dict()
        self.assertEqual(first["pull_request"]["number"],
                         second["pull_request"]["number"])
        self.assertEqual(first["publication_id"], second["publication_id"])
        self.assertEqual(len(self.api.pulls), 1, "a duplicate PR was opened")
        self.assertEqual(self.remote_head(), self.commit)

    def test_the_traceability_chain_is_complete(self):
        """Every hop from the planner request to the PR number, no guessing."""
        self.publisher.publish(self.dispatch.dispatch_id)
        row = self.store.publication(self.dispatch.dispatch_id)
        self.assertEqual(row.planner_request_id, self.dispatch.planner_request_id)
        self.assertEqual(row.dispatch_id, self.dispatch.dispatch_id)
        self.assertEqual(row.task_id, self.task_id)
        self.assertEqual(row.commit_sha, self.commit)
        self.assertEqual(row.branch, self.branch)
        self.assertEqual(row.pull_request_number, 42)
        # And back the other way, without a "latest PR" lookup anywhere.
        self.assertEqual(
            self.store.publication_by_task(self.task_id).pull_request_number, 42
        )
        approval = self.store.authority_event(row.planner_request_id)
        self.assertEqual(approval.subject_fingerprint,
                         self.dispatch.subject_fingerprint)

    def test_the_pr_body_carries_the_durable_facts_and_no_path(self):
        self.publisher.publish(self.dispatch.dispatch_id)
        _, _, body = [c for c in self.api.calls if c[0] == "POST"][-1]
        self.assertIn(self.dispatch.planner_request_id, body["body"])
        self.assertIn(self.task_id, body["body"])
        self.assertIn(self.commit[:12], body["body"])
        self.assertIn("does not merge", body["body"])
        self.assertNotIn(str(self.dir), body["body"])
        self.assertNotIn(FAKE_TOKEN, json.dumps(body))

    # -- the gates --------------------------------------------------------

    def test_an_unknown_dispatch_is_refused(self):
        view = self.publisher.publish("dsp_nonexistent")
        self.assertEqual(view.refusal["reason"], "dispatch_unknown")
        self.assertIsNone(self.remote_head())

    def test_an_unfinished_worker_is_refused(self):
        self.publisher = GitPublisher(
            store=self.store, tasks=self._task_double("running"),
            state_dir=self.state_dir,
        )
        view = self.publisher.publish(self.dispatch.dispatch_id)
        self.assertEqual(view.refusal["reason"], "worker_not_finished")
        self.assertIsNone(self.remote_head(), "an unfinished worker was pushed")

    def test_an_interrupted_task_with_a_real_commit_may_publish(self):
        """PR1f settles a recovered commit as interrupted. It is still a commit."""
        publisher = GitPublisher(
            store=self.store, tasks=self._task_double("interrupted"),
            state_dir=self.state_dir,
        )
        self.assertTrue(publisher.publish(self.dispatch.dispatch_id).to_dict()["published"])

    def test_a_worktree_with_no_worker_commit_is_refused(self):
        """A cut worktree sits on the project's own base commit."""
        subprocess.run(["rm", "-rf", str(self.tree.path)], check=True)
        git(self.repo, "worktree", "prune")
        # The branch survives the worktree, so it has to go too before a fresh
        # one can be cut at the same name.
        git(self.repo, "branch", "-D", self.branch)
        self.cut_worktree()
        view = self.publisher.publish(self.dispatch.dispatch_id)
        self.assertEqual(view.refusal["reason"], "no_worker_commit")
        self.assertIsNone(self.remote_head())

    def test_a_missing_worktree_is_refused(self):
        subprocess.run(["rm", "-rf", str(self.tree.path)], check=True)
        view = self.publisher.publish(self.dispatch.dispatch_id)
        self.assertEqual(view.refusal["reason"], "worktree_missing")
        self.assertIsNone(self.remote_head())

    def test_a_worktree_on_the_wrong_branch_is_refused(self):
        git(self.tree.path, "checkout", "-q", "-b", "somebody-elses")
        view = self.publisher.publish(self.dispatch.dispatch_id)
        self.assertEqual(view.refusal["reason"], "branch_mismatch")
        self.assertIsNone(self.remote_head())

    def test_an_unresolvable_project_is_refused(self):
        git(self.repo, "remote", "set-url", "origin", "https://gitlab.com/acme/widget")
        with self.assertRaises(remote.RemoteUnresolved):
            self.publisher.publish(self.dispatch.dispatch_id)
        self.assertIsNone(self.remote_head())

    def test_a_missing_credential_is_publisher_auth_required_not_worker_failure(self):
        credential.credentials_file(self.state_dir).unlink()
        view = self.publisher.publish(self.dispatch.dispatch_id)
        self.assertEqual(view.refusal["reason"], "publisher_auth_required")
        self.assertIsNone(self.remote_head())
        # The worker's commit is untouched and still local.
        self.assertEqual(
            git(self.tree.path, "rev-parse", "HEAD"), self.commit
        )

    def test_a_changed_commit_after_a_publish_is_refused(self):
        self.publisher.publish(self.dispatch.dispatch_id)
        (self.tree.path / "calc.py").write_text("# rewritten\n")
        worktree.commit_all(self.tree, message="worker: another",
                            author="Cofferdam Worker", email=WORKER_EMAIL)
        view = self.publisher.publish(self.dispatch.dispatch_id)
        self.assertEqual(view.refusal["reason"], "commit_changed")

    # -- crash windows ----------------------------------------------------

    def test_a_push_that_landed_before_the_crash_is_discovered(self):
        """Crash after push, before recording. Recovery looks; it does not push."""
        self.publisher.publish(self.dispatch.dispatch_id)
        pushed = self.remote_head()
        # Rewind Cofferdam's record to the pre-push state, as a crash would.
        from cofferdam.workstation.planner.store import Publication

        row = self.store.publication(self.dispatch.dispatch_id)
        self.store.upsert_publication(
            Publication(**{**row.__dict__, "state": service.STATE_PENDING,
                           "push_state": None, "pull_request_number": None,
                           "pull_request_url": None, "pull_request_state": None})
        )
        tally = self.publisher.reconcile_after_restart()
        self.assertEqual(tally, {service.STATE_PUBLISHED: 1}, tally)
        settled = self.store.publication(self.dispatch.dispatch_id)
        self.assertEqual(settled.state, service.STATE_PUBLISHED)
        self.assertEqual(settled.pull_request_number, 42)
        self.assertEqual(self.remote_head(), pushed)
        self.assertEqual(len(self.api.pulls), 1, "recovery created a second PR")

    def test_a_pr_created_before_the_crash_is_linked_not_duplicated(self):
        self.publisher.publish(self.dispatch.dispatch_id)
        from cofferdam.workstation.planner.store import Publication

        row = self.store.publication(self.dispatch.dispatch_id)
        self.store.upsert_publication(
            Publication(**{**row.__dict__, "state": service.STATE_PUSHED,
                           "pull_request_number": None, "pull_request_url": None})
        )
        self.publisher.reconcile_after_restart()
        self.assertEqual(
            self.store.publication(self.dispatch.dispatch_id).pull_request_number, 42
        )
        self.assertEqual(len(self.api.pulls), 1)

    def test_reconciliation_never_pushes(self):
        """A publication interrupted before its push stays interrupted."""
        from cofferdam.workstation.planner.store import Publication

        self.publisher._record(
            self.publisher._gate(self.dispatch.dispatch_id),
            state=service.STATE_PENDING,
        )
        self.assertIsNone(self.remote_head())
        tally = self.publisher.reconcile_after_restart()
        self.assertIsNone(self.remote_head(), "reconciliation pushed a branch")
        self.assertEqual(tally, {"branch_not_published": 1}, tally)

    def test_a_remote_that_moved_is_reported_not_overwritten(self):
        self.publisher.publish(self.dispatch.dispatch_id)
        other = self.dir / "other"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        git(other, "checkout", "-q", self.branch)
        (other / "theirs.txt").write_text("theirs\n")
        git(other, "add", "-A")
        git(other, "commit", "-qm", "theirs")
        git(other, "push", "-q", "origin", self.branch)
        theirs = self.remote_head()

        from cofferdam.workstation.planner.store import Publication

        row = self.store.publication(self.dispatch.dispatch_id)
        self.store.upsert_publication(
            Publication(**{**row.__dict__, "state": service.STATE_PENDING})
        )
        tally = self.publisher.reconcile_after_restart()
        self.assertEqual(tally, {"remote_mismatch": 1}, tally)
        self.assertEqual(self.remote_head(), theirs, "the remote was overwritten")
        self.assertTrue(
            self.store.publication(self.dispatch.dispatch_id).needs_attention
        )

    # -- what publishing never does ---------------------------------------

    def test_nothing_merges_deploys_or_reruns(self):
        self.publisher.publish(self.dispatch.dispatch_id)
        for method, path, _ in self.api.calls:
            self.assertNotIn("/merge", path)
            self.assertNotIn("/deployments", path)
            self.assertNotIn("/actions", path)
        # The planner request and the dispatch are untouched.
        self.assertEqual(
            self.store.get(self.dispatch.planner_request_id).status, "succeeded"
        )
        self.assertEqual(len(self.store.recent_dispatches(limit=50)), 1)

    def test_main_on_the_remote_is_never_written(self):
        before = subprocess.run(["git", "rev-parse", "refs/heads/main"],
                                cwd=self.bare, capture_output=True, text=True).stdout
        self.publisher.publish(self.dispatch.dispatch_id)
        after = subprocess.run(["git", "rev-parse", "refs/heads/main"],
                               cwd=self.bare, capture_output=True, text=True).stdout
        self.assertEqual(after, before)

    def test_the_view_says_cofferdam_did_not_merge(self):
        payload = self.publisher.publish(self.dispatch.dispatch_id).to_dict()
        self.assertFalse(payload["merged_by_cofferdam"])
        self.assertFalse(payload["human_action_needed"])
