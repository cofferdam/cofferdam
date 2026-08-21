"""The bounded development worker: one approved prompt, one contained process.

What this adapter is for
------------------------

Task Core already owns identity, lifecycle, persistence, events and policy, and
this adapter owns the one thing an adapter owns: making something happen and
reporting what happened. What is new is *what* happens — a model with a shell,
editing a real repository — and everything unusual in this file exists to make
that bounded.

Three things happen before the model sees anything, in this order, and each is
a refusal if it fails:

1. **containment is confirmed**, not assumed. No ``bubblewrap`` means no worker.
2. **an isolated worktree is cut** by Cofferdam from the core-resolved project
   root, on a code-owned branch, outside every project checkout.
3. **Cofferdam's own Claude session is required to be usable** — one durable
   config root it owns, never a copy of the operator's credential (:mod:`...worker.session`).

Only then is a process started, inside a namespace where the rest of the machine
is absent rather than denied.

Why it refuses rather than degrades
-----------------------------------

If containment is unavailable this adapter reports itself unavailable and
``start`` refuses. It does **not** fall back to running the model uncontained.
A fallback would mean the isolation guarantee silently became a comment on the
first host that lacked a package, which is the failure mode that makes security
properties worthless — and a task that refused to start is trivially recoverable
where a worker that roamed the filesystem is not.

The prompt reaches the process on **stdin**
-------------------------------------------

Never as an argument, exactly as the planner's does. A prompt on a command line
is a prompt that can be read as a flag, can appear in ``ps`` output, and is
bounded by ``ARG_MAX``. What the model receives is the approved worker prompt
Cofferdam persisted, preceded by a code-owned execution contract — and those two
are separated by a marker so that "what exactly was approved" stays answerable
afterwards.

What this adapter never does
----------------------------

Merge anything. Push to a protected branch. Deploy. Activate a slot. Touch
another project. Start a second worker. Call the planner again. None of those is
prevented by a sentence in a prompt: the first four are outside the namespace or
outside the branch policy, and the last two have no code path here at all.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ....worker import checks, journal, sandbox, session, worktree
from ...models import EVENT_PROGRESS, MAX_OUTPUT_CHARS, bounded_text
from ..protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from . import cli

ADAPTER_ID = "claude-code-worker"
DISPLAY_NAME = "Claude Code development worker"
DESCRIPTION = (
    "Runs one approved development step in an isolated Git worktree, contained "
    "so it can reach nothing else on this machine."
)

#: The marker that separates Cofferdam's execution contract from the model's own
#: approved prompt in what is written to stdin.
#:
#: Present so that the two identities stay distinguishable *in the bytes that
#: were sent*, not only in the database. The question "what exact planner prompt
#: did Cofferdam send" has to be answerable from the delivered payload, and a
#: contract concatenated onto a prompt with no boundary would make the answer a
#: matter of remembering how the concatenation used to work.
PROMPT_SEPARATOR = "\n\n===== APPROVED DEVELOPMENT STEP =====\n\n"

#: Cofferdam's own instructions to the worker. Code-owned, never model-authored,
#: and deliberately about *boundaries* rather than about the task — the task is
#: the approved prompt, and this text must never be mistaken for it.
#:
#: None of this is the security boundary. The namespace is. This tells a
#: cooperative worker what the shape of the job is so it does not waste turns
#: discovering the walls.
EXECUTION_CONTRACT = """You are a Cofferdam development worker.

You are running inside an isolated Git worktree at {worktree}. That directory is
the only part of this machine you can reach or write to; nothing outside it
exists in your filesystem. You are on branch {branch}, cut from {base}.

Your job is the approved development step given below the separator. Do that
step and nothing beyond it.

You have file tools only: read, write, edit, glob and grep, all within this
directory. You have no shell and cannot run commands — that is deliberate, not a
malfunction. After you finish, Cofferdam runs the project's own checks itself in
a separate sandbox and makes the commit; you do not need to do either, and you
cannot.

So: make the edits the step calls for, and stop. Do not try to run tests, do not
try to commit, and do not report that you did either.

If the step is ambiguous or you cannot complete it safely, stop and say so
plainly. An honest incomplete report is worth more than a confident wrong one.

When you finish, report which files you changed and why. Do not claim any test
passed — you did not run one, and Cofferdam will.
"""


class ClaudeCodeWorkerAdapter(TaskAdapter):
    """One contained development worker per task."""

    adapter_id = ADAPTER_ID
    display_name = DISPLAY_NAME
    description = DESCRIPTION

    def __init__(
        self,
        *,
        state_dir: Optional[Path] = None,
        timeout_seconds: float = cli.PROFILE_TIMEOUT_SECONDS,
        project_check: Optional[str] = None,
    ) -> None:
        # Resolved from host configuration when nobody said, never from a
        # request. It decides where worktrees and worker homes are created, and
        # it is *not* a parameter of ``build_registry`` — that table takes
        # booleans and nothing else, so an adapter that needs a location
        # resolves it the way the CLI path is resolved.
        self._state_dir = Path(
            state_dir if state_dir is not None else worktree.default_state_dir()
        )
        self._timeout = float(timeout_seconds)
        # Which host-owned check runs after the edits: a key into the closed
        # table in `worker.checks`, never a command.
        #
        # Named `project_check` rather than `check_id`, deliberately. M2K
        # reserves that vocabulary for *criterion* evaluation — the thing that
        # decides whether acceptance criteria are met — and this is not that. A
        # project's own test command run as an observation beside a dispatch must
        # not borrow the name of the thing that judges acceptance, because the
        # whole point of the boundary is that this result does not judge it.
        self._project_check = project_check
        self._lock = threading.RLock()
        self._processes: Dict[str, subprocess.Popen] = {}
        self._cancelled: set = set()

    # -- declaration ----------------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True,
            cancel=True,
            final_result=True,
            # No follow-up and no clarifications: a development step is one
            # approved unit of work. A worker that could be given more text
            # mid-run would be receiving instructions nobody approved.
            followup=False,
            clarifications=False,
            approvals=False,
            structured_progress=False,
            # PR1f. **This does not mean the work resumes**, and the flag's name
            # invites exactly that misreading. All Task Core does with it is
            # choose a target state: a task whose adapter declares this is parked
            # in `recovery_required` instead of being settled as `interrupted`,
            # which makes it visible to a pass that can go and look at what
            # actually happened on disk.
            #
            # The `recover()` hook on the protocol stays unimplemented here and
            # Task Core never calls it. Reconciliation lives in
            # `WorkerDispatchService.reconcile_after_restart`, because deciding
            # what an interrupted dispatch *was* needs the dispatch, the project
            # registry and Git — none of which an adapter method is given.
            #
            # Declaring it is only honest because `start` now writes the phase
            # journal below. Without those markers a restart could not tell a
            # commit that was attempted from one that never was, and parking a
            # task for a reconciler that cannot reconcile it would be worse than
            # settling it as interrupted.
            recover_after_restart=True,
        )

    def available(self) -> bool:
        contained, _ = sandbox.available()
        return contained and cli.find_executable() is not None

    def unavailable_reason(self) -> Optional[str]:
        contained, reason = sandbox.available()
        if not contained:
            return reason
        if cli.find_executable() is None:
            return "the Claude Code CLI is not installed on this host"
        return None

    # -- the one operation ----------------------------------------------------

    def start(self, context: TaskContext) -> AdapterOutcome:
        """Prepare containment, then run one worker inside it.

        Every failure before the process starts leaves nothing running, and the
        ordering is what makes that true: containment is checked, the Claude
        session is checked, and the worktree is cut before anything is launched.
        An unauthenticated session therefore costs a refusal, not a half-run
        dispatch with a branch and a worktree behind it.
        """
        contained, reason = sandbox.available()
        if not contained:
            # Refused, never downgraded to an uncontained run.
            raise AdapterRefusal(
                "this host cannot contain a development worker", detail=reason
            )
        executable = cli.find_executable()
        if executable is None:
            raise AdapterRefusal("the Claude Code CLI is not installed on this host")

        # Checked **before** a worktree is cut and before anything is launched.
        # An unauthenticated session is a condition a person fixes, not a failure
        # to discover halfway through a dispatch, and refusing here leaves no
        # worktree, no branch and no process behind.
        try:
            session_config = session.require_usable(self._state_dir)
        except session.WorkerSessionUnavailable as exc:
            raise AdapterRefusal(str(exc), detail=exc.status)

        try:
            tree = worktree.prepare(
                project_id=context.project_id,
                project_root=context.project_root,
                task_id=context.task_id,
                state_dir=self._state_dir,
            )
        except worktree.WorktreeError as exc:
            raise AdapterRefusal(
                "no isolated worktree could be prepared for this step",
                detail=exc.detail or str(exc),
            )

        try:
            plan = sandbox.build_plan(
                worktree=tree.path,
                cli_directory=cli.resolve_cli_directory(executable),
                command=tuple(
                    cli.build_interior_argv(
                        interior_cli=sandbox.INTERIOR_CLI,
                        interior_worktree=sandbox.INTERIOR_WORKTREE,
                    )
                ),
                session_config=session_config,
            )
        except sandbox.SandboxUnavailable as exc:
            raise AdapterRefusal(
                "the worker containment could not be built", detail=exc.detail or str(exc)
            )

        # From here on every externally visible phase is bracketed: the intent is
        # recorded durably, the operation runs, the observed result is recorded.
        # A crash between the two halves is what makes a phase *a question* for
        # `worker.reconcile` rather than an unknown — see `worker.journal`.
        note = self._note(context)
        note(
            journal.PHASE_PREPARED,
            base_commit=tree.base_commit,
            detail=tree.branch,
        )

        payload = build_worker_payload(context.prompt, tree)
        started = time.time()
        note(journal.PHASE_WORKER_RUNNING)
        # Serialized across dispatches: two CLI processes refreshing one token
        # file could each rotate it and leave the loser holding a superseded one
        # -- the very defect PR1g exists to fix. The lock covers the Claude
        # invocation and nothing else; the checks and the commit touch no
        # credential and must not be serialized behind it.
        try:
            with session.held(self._state_dir):
                result, failure = self._run(context.task_id, plan, payload)
        except session.WorkerSessionUnavailable as exc:
            result, failure = None, ("worker_session_busy", str(exc))
        elapsed_ms = int((time.time() - started) * 1000)
        # Recorded for a failed worker too. "The worker ran and did not finish"
        # and "the worker never ran" are different facts, and recovery is the
        # thing that needs them apart.
        note(
            journal.PHASE_WORKER_RETURNED,
            failure_code=failure[0] if failure is not None else None,
        )

        if failure is not None:
            return self._outcome(context, tree, None, failure, elapsed_ms, None, None)

        # -- phase two: the project's own checks, in a namespace with no
        # -- credential and no network. Cofferdam runs this; the worker could
        # -- not have, and that is what makes the result an observation.
        note(journal.PHASE_CHECKS_RUNNING)
        check = checks.run(worktree=tree.path, check=self._project_check)
        note(
            journal.PHASE_CHECKS_COMPLETED,
            check=check.check,
            exit_zero=check.exit_zero,
        )

        # -- phase three: the commit, also host-owned. A model holding a
        # -- provider credential should not be the thing authoring commits.
        #
        # `commit_pending` is written **before** `git commit` runs, and that
        # ordering is the whole reason a restart cannot produce a duplicate
        # commit: a crash in this window leaves an open intent, and recovery
        # answers it by asking Git rather than by committing again.
        note(journal.PHASE_COMMIT_PENDING)
        commit, commit_failure = self._commit(tree, check)
        note(journal.PHASE_COMMITTED, commit=commit)

        return self._outcome(
            context, tree, result, commit_failure, elapsed_ms, check, commit
        )

    def _note(self, context: TaskContext):
        """A journal writer bound to one dispatch. Never raises — see the module.

        Bound here rather than passed around so that no call site can write a
        phase for a different task: both ids come from the context this dispatch
        was built from.
        """

        def note(phase: str, **facts) -> None:
            journal.record(
                self._state_dir,
                context.project_id,
                context.task_id,
                phase,
                **facts,
            )

        return note

    def _commit(self, tree, check):
        """Commit the worker's edits. Runs whether or not the check passed.

        A failing check is information, not a reason to throw the work away: the
        branch is isolated, nothing downstream consumes it automatically, and a
        commit is what makes the attempt reviewable at all. The check result
        travels beside it rather than gating it.
        """
        try:
            message = (
                "worker: approved development step\n\n"
                f"Checks: {check.check} "
                f"{'exited 0' if check.exit_zero else 'did not exit 0'}.\n"
                "Authored by a Cofferdam development worker; not reviewed."
            )
            return (
                worktree.commit_all(
                    tree,
                    message=message,
                    author=cli.GIT_AUTHOR_NAME,
                    email=cli.GIT_AUTHOR_EMAIL,
                ),
                None,
            )
        except worktree.WorktreeError as exc:
            return None, ("worker_commit_failed", exc.detail or str(exc))

    def _run(
        self, task_id: str, plan: sandbox.SandboxPlan, payload: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[str, str]]]:
        """Launch, feed stdin, wait. Returns ``(parsed_result, failure)``."""
        try:
            process = subprocess.Popen(
                list(plan.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # The child's environment is set by ``--setenv`` inside the
                # namespace; the launcher itself gets nothing inherited, so a
                # variable in the daemon's environment cannot reach the worker.
                env={"PATH": "/usr/bin:/bin"},
            )
        except OSError as exc:
            return None, ("worker_launch_failed", str(exc))

        with self._lock:
            self._processes[task_id] = process

        try:
            stdout, stderr = process.communicate(payload, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            self._terminate(process)
            return None, ("worker_timeout", f"the worker did not finish in {int(self._timeout)}s")
        finally:
            with self._lock:
                self._processes.pop(task_id, None)

        with self._lock:
            was_cancelled = task_id in self._cancelled
            self._cancelled.discard(task_id)
        if was_cancelled:
            return None, ("worker_cancelled", "the worker was cancelled")

        parsed = _parse_result(stdout)
        if parsed is None:
            combined = stderr or stdout or ""
            # An unusable session is not an implementation failure, and calling
            # it one sends a person to debug code when what they need is a login.
            # Classified from the CLI's own words — see `session`, which also
            # explains why a non-auth failure must never be relabelled this way.
            auth = session.classify_auth_failure(combined)
            if auth is not None:
                return None, (
                    "worker_auth_required",
                    _scrub(session.SENTENCES.get(auth, combined))[:500],
                )
            return None, (
                "worker_envelope_invalid",
                _scrub(combined or "the worker produced no readable result")[:500],
            )
        return parsed, None

    def _terminate(self, process: subprocess.Popen) -> None:
        """Stop one worker. ``--die-with-parent`` covers what this misses."""
        try:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        except Exception:  # pragma: no cover - the process is already gone
            pass

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        """Stop **this task's** worker. Scoped by task id, never by pattern.

        The process is found in this adapter's own table, keyed by the task it
        belongs to. There is no search by name, no process-group sweep and no
        pattern match — the mechanisms by which a cancel becomes somebody else's
        outage.
        """
        with self._lock:
            process = self._processes.get(context.task_id)
            if process is None:
                raise AdapterRefusal("this task has no running worker to stop")
            self._cancelled.add(context.task_id)
        self._terminate(process)
        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS, text="The development worker was stopped."
                ).bounded(),
            ),
            requested_state="cancelled",
        )

    def shutdown(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
            self._processes.clear()
        for process in processes:
            self._terminate(process)

    # -- reporting ------------------------------------------------------------

    def _outcome(
        self,
        context: TaskContext,
        tree: "worktree.DevelopmentWorktree",
        result: Optional[Dict[str, Any]],
        failure: Optional[Tuple[str, str]],
        elapsed_ms: int,
        check: Optional["checks.CheckResult"] = None,
        commit: Optional[str] = None,
    ) -> AdapterOutcome:
        """Turn what happened into events and one requested state.

        Everything the worker *said* is reported as a claim. What Cofferdam
        observed for itself — the branch, the commit the worktree is on now — is
        read by Cofferdam running Git, and is reported separately. PR1f is what
        reconciles the two; this method's job is to keep them from being mixed up
        before it can.
        """
        observed = _observe(tree)
        events: List[AdapterEvent] = [
            AdapterEvent(
                event_type=EVENT_PROGRESS,
                text=f"Worker ran in an isolated worktree on {tree.branch}.",
                detail=f"{elapsed_ms}ms",
            ).bounded()
        ]

        if failure is not None:
            code, message = failure
            events.append(
                AdapterEvent(
                    event_type=EVENT_PROGRESS, text="The development worker did not finish."
                ).bounded()
            )
            return AdapterOutcome(
                events=tuple(events),
                requested_state="cancelled" if code == "worker_cancelled" else "failed",
                failure_code=code,
                failure_message=message[:1000],
            )

        assert result is not None
        summary = result.get("result")
        if result.get("is_error"):
            # The CLI reports an unusable session *inside* a well-formed result
            # envelope, so this is the path a dead session actually takes — it is
            # how PR1f's validation saw `worker_reported_error: Failed to
            # authenticate`. Classified here too, or the one failure a person can
            # actually fix would keep arriving labelled as the model's mistake.
            auth = session.classify_auth_failure(str(summary or ""))
            if auth is not None:
                return AdapterOutcome(
                    events=tuple(events),
                    requested_state="failed",
                    failure_code="worker_auth_required",
                    failure_message=session.SENTENCES.get(
                        auth, "Cofferdam's Claude worker session needs login."
                    ),
                )
            return AdapterOutcome(
                events=tuple(events),
                requested_state="failed",
                failure_code="worker_reported_error",
                failure_message=(
                    bounded_text(_scrub(summary), 1000) or "the worker reported an error"
                ),
            )

        events.append(
            AdapterEvent(
                event_type=EVENT_PROGRESS, text=_observation_line(observed)
            ).bounded()
        )
        if check is not None:
            events.append(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text=(
                        f"Cofferdam ran {check.check} in a credential-free "
                        f"sandbox: {'exited 0' if check.exit_zero else 'did not exit 0'}."
                    ),
                    detail=check.failure,
                ).bounded()
            )
        return AdapterOutcome(
            events=tuple(events),
            requested_state="completed",
            # Two different kinds of sentence, kept apart in the text itself.
            # What the worker said is a claim; what Cofferdam ran is an
            # observation. PR1f is where the pair becomes a verdict.
            final_result=bounded_text(
                _compose_result(_scrub(summary), check, commit), MAX_OUTPUT_CHARS
            ),
        )


def build_worker_payload(approved_prompt: str, tree: "worktree.DevelopmentWorktree") -> str:
    """What is written to the worker's stdin: contract, separator, exact prompt.

    **The approved prompt is appended byte-for-byte.** It is not reformatted,
    summarized, truncated, re-wrapped or interpolated into a template. The
    contract is code-owned text that precedes it and the separator is a constant,
    so ``payload.split(PROMPT_SEPARATOR, 1)[1]`` is exactly what was approved —
    which is the property :func:`delivered_prompt` exists to let a test assert.
    """
    contract = EXECUTION_CONTRACT.format(
        worktree=sandbox.INTERIOR_WORKTREE,
        branch=tree.branch,
        base=tree.base_commit[:12],
    )
    return contract + PROMPT_SEPARATOR + approved_prompt


def delivered_prompt(payload: str) -> str:
    """The approved prompt recovered from a delivered payload.

    The other half of :func:`build_worker_payload`, and the reason the separator
    is a constant rather than a formatting flourish: "what exactly did Cofferdam
    send the worker" has to be answerable from the bytes, by anyone, later.
    """
    _, separator, prompt = payload.partition(PROMPT_SEPARATOR)
    return prompt if separator else payload


#: Shapes a provider credential takes, scrubbed from anything Cofferdam stores.
#:
#: Layer three, and the smallest of the three. The worker has no way to *send*
#: a credential, but its final message is the one channel that leaves the
#: namespace by design — so the text is filtered before it is persisted. This is
#: defence in depth over a per-dispatch credential copy, not the boundary.
_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}"),
    re.compile(r'"(?:access|refresh)Token"\s*:\s*"[^"]+"', re.IGNORECASE),
)


def _scrub(text: Optional[str]) -> Optional[str]:
    """Remove credential-shaped strings from worker output before it is stored."""
    if not text:
        return text
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def _compose_result(summary, check, commit) -> str:
    """The worker's claim and Cofferdam's observations, labelled as such."""
    parts = ["WORKER REPORT (a claim by the worker, not verified):", summary or "(none)"]
    parts.append("")
    parts.append("COFFERDAM OBSERVED:")
    if check is not None:
        parts.append(
            f"- check {check.check}: "
            + ("exited 0" if check.exit_zero else "did not exit 0")
            + (f" ({check.failure})" if check.failure else "")
        )
        if check.output.strip():
            parts.append("- check output:")
            parts.append(check.output[:4000])
    parts.append(
        "- commit: " + (commit if commit else "no commit (the worker changed nothing)")
    )
    return "\n".join(parts)


def _parse_result(stdout: Optional[str]) -> Optional[Dict[str, Any]]:
    """The CLI's ``--output-format json`` envelope, or ``None``.

    Never repaired and never regex-recovered. An unreadable envelope is a failed
    worker run, for the reason the planner gives about its own: the one thing a
    broken result must not become is a plausible-looking success.
    """
    if not stdout:
        return None
    text = stdout.strip()
    start = text.rfind("\n{")
    for candidate in ([text] if start < 0 else [text[start + 1 :], text]):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _observe(tree: "worktree.DevelopmentWorktree") -> Dict[str, Any]:
    """What **Cofferdam** can see in the worktree, by running Git itself.

    Separate from anything the worker said, on purpose. These are the beginnings
    of the machine-observed side of PR1f's reconciliation; today they are
    reported as context and are not treated as acceptance.
    """
    facts: Dict[str, Any] = {"branch": tree.branch, "commit": None, "committed": False}
    try:
        head = worktree.head_commit(tree.path)
    except worktree.WorktreeError:
        return facts
    facts["commit"] = head
    facts["committed"] = head != tree.base_commit
    return facts


def _observation_line(observed: Dict[str, Any]) -> str:
    if observed.get("committed"):
        return f"Cofferdam observed a new commit on {observed['branch']}."
    return f"Cofferdam observed no new commit on {observed['branch']}."


__all__ = [
    "ADAPTER_ID",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "EXECUTION_CONTRACT",
    "PROMPT_SEPARATOR",
    "ClaudeCodeWorkerAdapter",
    "build_worker_payload",
    "delivered_prompt",
]
