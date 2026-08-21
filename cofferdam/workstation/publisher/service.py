"""Publish one finished worker branch. The gates, the push, the pull request.

The signature is the security property, again
----------------------------------------------

::

    publish(dispatch_id)

No repository, no remote URL, no branch, no commit, no base, no path, no argv and
no GitHub parameter. The caller names *which finished dispatch to publish*;
Cofferdam loads what that means from durable state and reads the rest out of Git.
The substitution attack this closes — publish dispatch A's approval to repository
B — is not something to validate against, because there is no argument through
which B could arrive.

The order of the gates is the design
-------------------------------------

Everything that can refuse does so **before** the first external write. By the
time ``git push`` runs, Cofferdam has already established that this dispatch was
approved, that its task finished, that the worktree is the one that task owns,
that ``HEAD`` is the exact commit that was committed, that the branch is
code-owned, and that the repository is the one the project itself points at. A
publish that is going to be refused costs nothing on GitHub.

Recovery is reconciliation, not repetition
-------------------------------------------

GitHub is external state and Cofferdam can die after changing it, so both writes
are re-entrant by construction rather than by a flag:

* the push is fast-forward-only to an exact refspec, so re-running it either does
  nothing (*Everything up-to-date*) or fails — it can never overwrite;
* the pull request is looked up by exact ``head``/``base`` on an exact
  repository, so re-running finds the first one instead of opening a second.

That is PR1f's doctrine applied across a network boundary: look at what is
actually true, then record it. Nothing here retries blindly, and nothing here
re-runs a worker, re-sends a prompt or rewrites a commit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..planner.store import PlannerStore, Publication
from ..tasks.errors import ProjectDisabled, ProjectRootInvalid, ProjectUnknown
from ..worker import worktree
from . import credential, github, remote

#: What a publication can be. Closed, and deliberately not Task Core's
#: vocabulary: a task's lifecycle is about a worker process, and none of these
#: describe one.
STATE_PENDING = "pending"
STATE_PUSHED = "branch_published"
STATE_PUBLISHED = "published"
STATE_REFUSED = "refused"
STATE_INTERRUPTED = "interrupted"

#: The base every worker branch targets. Code-owned: there is no parameter that
#: selects a base, because a caller who could choose one could target a release
#: branch with a model's work.
DEFAULT_BASE_BRANCH = "main"

#: Terminal states of the worker task a publication may follow. ``interrupted``
#: is included on purpose — PR1f settles a *recovered commit* as interrupted, and
#: that commit is exactly as real as a completed one. What must be true is that a
#: commit exists, which is checked directly rather than inferred from the state.
PUBLISHABLE_TASK_STATES = frozenset({"completed", "interrupted"})

MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 4000


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_publication_id() -> str:
    from ..tasks.identity import new_task_id

    return "pub_" + new_task_id().split("_", 1)[1]


class PublishRefused(github.PublishRefused):
    """A publish this layer will not perform. Carries the rule that stopped it."""


@dataclass(frozen=True)
class PublicationView:
    """What a caller — and later a cockpit — gets back.

    No host path, no remote URL, no credential. ``repository`` is a name.
    """

    publication: Optional[Publication]
    refusal: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        if self.publication is None:
            return {"published": False, "refusal": self.refusal}
        payload = self.publication.to_dict()
        payload["published"] = self.publication.state == STATE_PUBLISHED
        payload["refusal"] = self.refusal
        # Stated here so nothing downstream has to rediscover it: a pull request
        # is a request for review. Cofferdam never merges one.
        payload["merged_by_cofferdam"] = False
        payload["human_action_needed"] = bool(self.publication.needs_attention) or (
            self.publication.state != STATE_PUBLISHED
        )
        return payload


class GitPublisher:
    """Cofferdam's host-owned publisher. Holds no process and no model."""

    def __init__(
        self,
        *,
        store: PlannerStore,
        tasks,
        state_dir: Optional[Path] = None,
        base_branch: str = DEFAULT_BASE_BRANCH,
        clock=_utc_now,
    ) -> None:
        self._store = store
        self._tasks = tasks
        self._state_dir = Path(
            state_dir if state_dir is not None else worktree.default_state_dir()
        )
        # Code-owned, and validated as publishable-target rather than trusted:
        # a base of `refs/heads/../..` or a branch this publisher may itself
        # write would both be wrong in different ways.
        self._base_branch = str(base_branch)
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    def view(self, dispatch_id: str) -> PublicationView:
        return PublicationView(publication=self._store.publication(dispatch_id))

    def status(self, *, reach: bool = False) -> Dict[str, Any]:
        """The publisher doctor. Never prints credential material."""
        return credential.describe(self._state_dir, reach=reach)

    # -- the one write -------------------------------------------------------

    def publish(self, dispatch_id: str) -> PublicationView:
        """Push one approved dispatch's branch and open its pull request.

        Idempotent. A second call for the same dispatch pushes nothing new and
        returns the same pull request, because both external operations are
        re-entrant and the publication row is keyed on the dispatch.
        """
        try:
            plan = self._gate(dispatch_id)
        except (PublishRefused, github.PublishRefused) as refusal:
            self._record_refusal(dispatch_id, refusal)
            return PublicationView(
                publication=self._store.publication(dispatch_id),
                refusal=refusal.to_dict(),
            )

        try:
            credentials_path = credential.require_usable(self._state_dir)
        except credential.PublisherCredentialUnavailable as exc:
            refusal = PublishRefused(
                str(exc), reason="publisher_auth_required", detail=exc.detail
            )
            self._record(plan, state=STATE_REFUSED, refusal=refusal)
            return PublicationView(
                publication=self._store.publication(dispatch_id),
                refusal=refusal.to_dict(),
            )

        # Recorded *before* the first external write, so a crash in the push
        # window leaves a row saying "we were about to push this exact commit"
        # rather than no evidence at all. Same intent-then-result ordering the
        # worker's phase journal uses.
        self._record(plan, state=STATE_PENDING)

        try:
            push_state, _ = github.push(
                worktree=plan.worktree_path,
                repository=plan.repository,
                branch=plan.branch,
                expected_commit=plan.commit,
                credentials_path=credentials_path,
            )
            self._record(plan, state=STATE_PUSHED, push_state=push_state)

            pull_request = github.find_pull_request(
                self._state_dir, plan.repository,
                branch=plan.branch, base=self._base_branch,
            ) or github.create_pull_request(
                self._state_dir, plan.repository,
                branch=plan.branch, base=self._base_branch,
                title=plan.title, body=plan.body,
            )
        except (PublishRefused, github.PublishRefused) as refusal:
            self._record(plan, state=STATE_INTERRUPTED, refusal=refusal)
            return PublicationView(
                publication=self._store.publication(dispatch_id),
                refusal=refusal.to_dict(),
            )
        except credential.PublisherCredentialUnavailable as exc:
            refusal = PublishRefused(
                str(exc), reason="publisher_auth_required", detail=exc.detail
            )
            self._record(plan, state=STATE_INTERRUPTED, refusal=refusal)
            return PublicationView(
                publication=self._store.publication(dispatch_id),
                refusal=refusal.to_dict(),
            )

        self._record(
            plan, state=STATE_PUBLISHED, push_state=push_state,
            pull_request=pull_request,
        )
        return PublicationView(publication=self._store.publication(dispatch_id))

    # -- recovery ------------------------------------------------------------

    def reconcile_after_restart(self) -> Dict[str, int]:
        """Settle publications a restart left mid-flight. **Looks, never repeats.**

        For each unfinished row: ask the remote what commit the branch is at, and
        ask GitHub whether a pull request exists for that exact head and base. A
        push that landed is discovered rather than repeated; a pull request that
        was created is linked rather than duplicated.

        Never pushes. A publication interrupted before its push stays
        interrupted and waits for an explicit ``publish`` — the same rule PR1f
        follows for a worker, and for the same reason: continuing a consequential
        operation because a process restarted is doing it on nobody's authority.
        """
        tally: Dict[str, int] = {}
        try:
            credentials_path = credential.require_usable(self._state_dir)
        except credential.PublisherCredentialUnavailable:
            return tally

        for row in self._store.unfinished_publications():
            outcome = self._reconcile_one(row, credentials_path)
            tally[outcome] = tally.get(outcome, 0) + 1
        return tally

    def _reconcile_one(self, row: Publication, credentials_path: Path) -> str:
        try:
            repository = remote.parse("https://github.com/" + row.repository)
            tree = worktree.worktree_path(
                self._state_dir, row.project_id, row.task_id
            )
            if not tree.is_dir():
                # ls-remote needs a repository to run from; without one the
                # remote cannot be read and guessing is not an option.
                return self._settle(row, STATE_INTERRUPTED, "worktree_missing")
            remote_commit = github.remote_branch_commit(
                worktree=tree, repository=repository,
                branch=row.branch, credentials_path=credentials_path,
            )
        except Exception:
            return self._settle(row, STATE_INTERRUPTED, "remote_unreadable")

        if remote_commit is None:
            return self._settle(row, STATE_INTERRUPTED, "branch_not_published")
        if remote_commit != row.commit_sha:
            # Something else is on this branch. Not overwritten, not adopted.
            return self._settle(row, STATE_INTERRUPTED, "remote_mismatch")

        pull_request = None
        try:
            pull_request = github.find_pull_request(
                self._state_dir, repository,
                branch=row.branch, base=row.base_branch,
            )
        except Exception:
            pass

        updated = Publication(
            **{
                **row.__dict__,
                "state": STATE_PUBLISHED if pull_request else STATE_PUSHED,
                "push_state": row.push_state or github.PUSH_ALREADY_CURRENT,
                "pull_request_number": (
                    pull_request.get("number") if pull_request else None
                ),
                "pull_request_url": pull_request.get("url") if pull_request else None,
                "pull_request_state": (
                    pull_request.get("state") if pull_request else None
                ),
                "failure_reason": None if pull_request else row.failure_reason,
                "failure_detail": None if pull_request else row.failure_detail,
                "needs_attention": 0 if pull_request else 1,
                "updated_at": self._clock(),
            }
        )
        self._store.upsert_publication(updated)
        return STATE_PUBLISHED if pull_request else STATE_PUSHED

    def _settle(self, row: Publication, state: str, reason: str) -> str:
        self._store.upsert_publication(
            Publication(
                **{
                    **row.__dict__,
                    "state": state,
                    "failure_reason": reason,
                    "needs_attention": 1,
                    "updated_at": self._clock(),
                }
            )
        )
        return reason

    # -- the gates -----------------------------------------------------------

    def _gate(self, dispatch_id: str) -> "_PublishPlan":
        """Everything that must be true, established from durable state and Git."""
        dispatch = self._dispatch(dispatch_id)
        if dispatch is None:
            raise PublishRefused(
                "there is no such dispatch", reason="dispatch_unknown"
            )

        record = self._store.get(dispatch.planner_request_id)
        if record is None or record.action != "PREPARE_WORKER_PROMPT":
            raise PublishRefused(
                "this dispatch did not come from an approved worker prompt",
                reason="not_a_worker_prompt",
            )
        authority = self._store.authority_event(dispatch.planner_request_id)
        if authority is None or authority.authority_action != "approve":
            raise PublishRefused(
                "this dispatch was never approved by a person",
                reason="not_approved",
            )
        if authority.subject_fingerprint != dispatch.subject_fingerprint:
            raise PublishRefused(
                "the approval does not bind this dispatch",
                reason="approval_mismatch",
            )

        task = self._task(dispatch.task_id)
        state = getattr(task, "state", None)
        if state not in PUBLISHABLE_TASK_STATES:
            raise PublishRefused(
                "this dispatch's worker has not finished",
                reason="worker_not_finished", detail=str(state),
            )

        project_root = self._project_root(dispatch.project_id)
        repository = remote.resolve(project_root)

        tree = worktree.worktree_path(
            self._state_dir, dispatch.project_id, dispatch.task_id
        )
        if not tree.is_dir():
            raise PublishRefused(
                "this dispatch's worktree is not on this host",
                reason="worktree_missing",
            )

        expected_branch = worktree.branch_name(dispatch.task_id)
        actual_branch, head = self._identity(tree)
        if actual_branch != expected_branch:
            raise PublishRefused(
                "this dispatch's worktree is not on its own branch",
                reason="branch_mismatch", detail=actual_branch,
            )
        # Belt to the braces: the branch is derived from the task id, and it is
        # still checked against the publisher's own policy before anything runs.
        github.publishable_branch(expected_branch)

        if not self._has_worker_commit(tree, head):
            raise PublishRefused(
                "this dispatch produced no Cofferdam worker commit to publish",
                reason="no_worker_commit",
            )

        recorded = self._store.publication(dispatch_id)
        if recorded is not None and recorded.commit_sha != head:
            # The worktree moved since the last attempt. Refused rather than
            # silently publishing something else under the same publication.
            raise PublishRefused(
                "this dispatch's commit changed since it was last published",
                reason="commit_changed", detail=recorded.commit_sha[:12],
            )

        return _PublishPlan(
            dispatch=dispatch,
            repository=repository,
            worktree_path=tree,
            branch=expected_branch,
            commit=head,
            title=_title(record, dispatch),
            body=_body(record, dispatch, head, repository),
        )

    def _dispatch(self, dispatch_id: str):
        for candidate in self._store.recent_dispatches(limit=200):
            if candidate.dispatch_id == dispatch_id:
                return candidate
        return None

    def _task(self, task_id: str):
        for name in ("get_task", "get"):
            getter = getattr(self._tasks, name, None)
            if getter is not None:
                try:
                    return getter(task_id)
                except Exception:
                    return None
        return None

    def _project_root(self, project_id: str) -> Path:
        """Re-resolved through the host registry, never taken from a caller."""
        registry = self._tasks.projects
        if callable(registry):  # pragma: no cover - a double may expose a method
            registry = registry()
        try:
            project = registry.get(project_id)
        except (ProjectUnknown, ProjectDisabled, ProjectRootInvalid) as exc:
            raise PublishRefused(
                "this dispatch's project is not available",
                reason="project_unresolved", detail=project_id,
            ) from exc
        root = getattr(project, "root", None)
        if root is None:
            raise PublishRefused(
                "this dispatch's project has no usable folder",
                reason="project_unresolved", detail=project_id,
            )
        return Path(root)

    def _identity(self, tree: Path):
        branch = remote._git(tree, ["rev-parse", "--abbrev-ref", "HEAD"])
        head = remote._git(tree, ["rev-parse", "HEAD"])
        if branch.returncode != 0 or head.returncode != 0:
            raise PublishRefused(
                "this dispatch's worktree could not be read",
                reason="worktree_unreadable",
            )
        return (branch.stdout or "").strip(), (head.stdout or "").strip()

    def _has_worker_commit(self, tree: Path, head: str) -> bool:
        """Whether ``HEAD`` is a commit Cofferdam's worker identity authored.

        The same test PR1f's reconciler uses, and for the same reason: a worktree
        cut and never committed to sits on the project's own base commit, which
        was authored by somebody else. Publishing that would push nothing of this
        dispatch's under this dispatch's name.
        """
        from ..tasks.adapters.claude_code_worker import cli

        author = remote._git(tree, ["log", "-1", "--format=%ae", head])
        if author.returncode != 0:
            return False
        return (author.stdout or "").strip() == cli.GIT_AUTHOR_EMAIL

    # -- persistence ---------------------------------------------------------

    def _record(
        self,
        plan: "_PublishPlan",
        *,
        state: str,
        push_state: Optional[str] = None,
        pull_request: Optional[Dict[str, Any]] = None,
        refusal: Optional[Exception] = None,
    ) -> None:
        existing = self._store.publication(plan.dispatch.dispatch_id)
        now = self._clock()
        self._store.upsert_publication(
            Publication(
                publication_id=(
                    existing.publication_id if existing else new_publication_id()
                ),
                dispatch_id=plan.dispatch.dispatch_id,
                planner_request_id=plan.dispatch.planner_request_id,
                task_id=plan.dispatch.task_id,
                project_id=plan.dispatch.project_id,
                workspace_id=plan.dispatch.workspace_id,
                repository=plan.repository.full_name,
                branch=plan.branch,
                base_branch=self._base_branch,
                commit_sha=plan.commit,
                state=state,
                push_state=push_state or (existing.push_state if existing else None),
                pull_request_number=(
                    pull_request.get("number") if pull_request else None
                ),
                pull_request_url=pull_request.get("url") if pull_request else None,
                pull_request_state=(
                    pull_request.get("state") if pull_request else None
                ),
                failure_reason=getattr(refusal, "reason", None),
                failure_detail=getattr(refusal, "detail", None),
                needs_attention=int(state != STATE_PUBLISHED),
                actor="cofferdam",
                source="publisher",
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
        )

    def _record_refusal(self, dispatch_id: str, refusal: Exception) -> None:
        """A refusal before the plan exists updates an existing row, or nothing.

        No row is invented for a dispatch that was never publishable: a
        publication record asserts a real relationship with a repository, and
        creating one for a refusal would put a repository name on something that
        was never going to reach it.
        """
        existing = self._store.publication(dispatch_id)
        if existing is None:
            return
        self._store.upsert_publication(
            Publication(
                **{
                    **existing.__dict__,
                    "state": STATE_REFUSED,
                    "failure_reason": getattr(refusal, "reason", None),
                    "failure_detail": getattr(refusal, "detail", None),
                    "needs_attention": 1,
                    "updated_at": self._clock(),
                }
            )
        )


@dataclass(frozen=True)
class _PublishPlan:
    """One publish, fully decided before anything external happens."""

    dispatch: Any
    repository: remote.GitHubRepository
    worktree_path: Path
    branch: str
    commit: str
    title: str
    body: str


def _title(record, dispatch) -> str:
    """A PR title from durable facts, with the model's summary as *material*.

    The planner's summary is model-authored text, so it informs the title and
    never decides anything: the repository, head, base and the act of opening a
    pull request are all host-owned. It is bounded and stripped of newlines so it
    cannot restructure the request it travels in.
    """
    summary = " ".join(str(getattr(record, "summary", "") or "").split())
    if not summary:
        summary = "approved development step"
    return f"Cofferdam worker: {summary}"[:MAX_TITLE_CHARS]


def _body(record, dispatch, commit: str, repository) -> str:
    """The traceability chain, rendered. Every line is a durable fact."""
    lines = [
        "Opened by Cofferdam's host-owned Git publisher.",
        "",
        "| | |",
        "|---|---|",
        f"| planner request | `{dispatch.planner_request_id}` |",
        f"| approved subject | `{dispatch.subject_fingerprint[:16]}…` |",
        f"| dispatch | `{dispatch.dispatch_id}` |",
        f"| worker task | `{dispatch.task_id}` |",
        f"| commit | `{commit[:12]}` |",
        "",
        "The commit was authored by a Cofferdam development worker and has "
        "**not** been reviewed. Cofferdam ran the project's own checks as an "
        "execution observation, which is not an acceptance judgement.",
        "",
        "Cofferdam does not merge pull requests.",
    ]
    return "\n".join(lines)[:MAX_BODY_CHARS]


__all__ = [
    "DEFAULT_BASE_BRANCH",
    "PUBLISHABLE_TASK_STATES",
    "STATE_INTERRUPTED",
    "STATE_PENDING",
    "STATE_PUBLISHED",
    "STATE_PUSHED",
    "STATE_REFUSED",
    "GitPublisher",
    "PublicationView",
    "PublishRefused",
    "new_publication_id",
]
