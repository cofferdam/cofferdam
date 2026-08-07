"""The Claude Code adapter: one task, one process, one session, told truthfully.

This is the only file in the package that knows what a Task Core state means. It
takes what :mod:`.process` and :mod:`.frames` observed, checks it against what
:mod:`.evidence` saw for itself, and turns the pair into an
:class:`~..protocol.AdapterOutcome` — a *report*, which the core is free to
refuse.

The rules it is written to
--------------------------

**Exit code zero is not success.** The CLI exits 0 whenever it ran, including
runs whose ``result`` frame says ``is_error``. Completion requires a ``result``
frame with ``is_error`` false *and* result text. A process that exits 0 having
said nothing is a failure, and the message says exactly that.

**Assistant text is not evidence.** "I updated the file" is recorded as
``adapter_reported`` and stays there. What appears as ``git_observed`` came from
Cofferdam running Git itself, in :mod:`.evidence`, and there is no code path
that promotes one to the other.

**A capability is a fact about today.** ``recover_after_restart`` is ``False``
because no recovery is implemented. ``approvals`` is ``False`` because this
adapter cannot answer a permission request — when one is refused it says so and
waits for a person, which is a different and honest thing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional

from ...models import (
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_PROGRESS,
    EVIDENCE_OS_OBSERVED,
    EVIDENCE_PROCESS,
    MAX_RESULT_CHARS,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_READY_FOR_FOLLOWUP,
    STATE_WAITING_FOR_USER,
    WAITING_APPROVAL,
    WAITING_AUTHENTICATION,
    EvidenceReference,
)
from ..protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)
from . import cli
from .evidence import git_evidence, observe_git
from .process import ClaudeRun

ADAPTER_ID = "claude-code"
DISPLAY_NAME = "Claude Code"
DESCRIPTION = (
    "Runs Claude Code inside one approved project. It can read and edit files "
    "there; it has no shell, and it cannot leave the project folder."
)

#: One at a time, and the number is deliberate rather than provisional. Each
#: task holds a live CLI process and a subscription session for its whole
#: lifetime, and a personal workstation running several of those at once would
#: be spending somebody's quota on work they have lost track of. A host may
#: raise it in source; a client cannot see it, let alone set it.
DEFAULT_MAX_CONCURRENT_TASKS = 1

#: How long a finished turn waits for its process to prove it is staying alive
#: before the task is reported as able to take a follow-up. See :meth:`_settle`
#: — a `result` frame is parsed before stdout reaches EOF, so this is the only
#: way to tell "waiting for you" from "about to exit".
TURN_SETTLE_GRACE_SECONDS = 0.75

#: How long a follow-up waits for the current turn to finish before it is
#: refused. Long enough for a normal turn boundary, short enough that a phone is
#: not left holding a request.
FOLLOWUP_TURN_WAIT_SECONDS = 2.0

#: What the PWA is told about this adapter's boundaries. Bounded sentences, not
#: a configuration dump — a person reading a phone needs to know what it will
#: refuse to do, not which flags produced that.
LIMITATIONS = (
    "Claude has no shell here. It can read, search, create and edit files "
    "inside the chosen project, and nothing else.",
    "It cannot leave the project folder.",
    "A task is not resumed if Cofferdam restarts — it is reported as "
    "interrupted, and its output is kept.",
    "Cofferdam runs its own Git checks. Anything Claude only claims to have "
    "done is labelled as its claim, not as something Cofferdam saw.",
    "Never type a password, one-time code or token into a prompt or follow-up.",
)


#: Phrases that mean the CLI could not authenticate, matched case-insensitively
#: against the *result text it already produced*. This is not prose parsing
#: standing in for a structured signal — the CLI offers no structured auth
#: subtype mid-run, and the honest alternative was reporting an expired sign-in
#: as a task failure, which sends somebody to debug their prompt. The
#: limitation is stated in the guide.
_AUTH_FAILURE_MARKERS = (
    "oauth access token has expired",
    "failed to authenticate",
    "authentication_failed",
    "please run /login",
    "invalid api key",
)


def _is_authentication_failure(text: Optional[str]) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _AUTH_FAILURE_MARKERS)


def _failure_code(subtype: Optional[str]) -> str:
    """A failure code that does not contradict itself."""
    if not subtype or subtype == "success":
        return "claude_error"
    return "claude_" + subtype


class ClaudeCodeAdapter(TaskAdapter):
    """One instance per process, holding every live run.

    Registered only when the host explicitly enabled it. There is no client
    input anywhere in this constructor: the executable is *found*, never
    supplied, and the concurrency limit is a keyword argument that only
    :func:`~..build_registry` passes.
    """

    adapter_id = ADAPTER_ID
    display_name = DISPLAY_NAME
    description = DESCRIPTION

    def __init__(
        self,
        *,
        executable: Optional[Path] = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_TASKS,
        run_factory=None,
        auth_probe=None,
    ) -> None:
        self._executable = executable if executable is not None else cli.find_executable()
        self._max_concurrent = max(1, int(max_concurrent))
        self._run_factory = run_factory or ClaudeRun
        self._auth_probe = auth_probe or cli.probe_authentication
        self._runs: Dict[str, ClaudeRun] = {}
        #: What was last reported to the core, per task. `inspect` is called on
        #: every read of a task detail, and without this it answered the same
        #: question with the same answer forever — 160 events for a task that
        #: had about ten things to say. The store refuses consecutive duplicates
        #: as well; this is the half that stops them being generated at all, and
        #: with them the Git probes that produced the evidence.
        self._reported: Dict[str, Dict[str, object]] = {}
        self._lock = threading.RLock()

    # -- what this adapter is ------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True,
            followup=True,
            cancel=True,
            structured_progress=True,
            final_result=True,
            authentication_waits=True,
            # False, and it is the honest answer rather than a placeholder.
            # Answering a Claude permission request would need a channel this
            # milestone does not build; a refused tool is reported and waits for
            # a person instead of being silently approved.
            approvals=False,
            # False until a complete safe reattachment design exists and has
            # been proven. "Possible later" is not a capability.
            recover_after_restart=False,
        )

    def available(self) -> bool:
        return self._executable is not None

    def unavailable_reason(self) -> Optional[str]:
        if self._executable is None:
            return "Claude Code is not installed on this workstation"
        return None

    def describe(self) -> Dict[str, object]:
        described = super().describe()
        described["limitations"] = list(LIMITATIONS)
        described["max_concurrent_tasks"] = self._max_concurrent
        described["tools"] = list(cli.PROFILE_TOOLS)
        return described

    # -- lifecycle -----------------------------------------------------------

    def start(self, context: TaskContext) -> AdapterOutcome:
        """Launch one process for this task and return as soon as it is real.

        Does not wait for the work. The core turns the task ``running`` after
        this returns, and everything afterwards arrives through
        :meth:`inspect`.
        """
        if self._executable is None:
            raise AdapterRefusal("Claude Code is not installed on this workstation")
        if not cli.verify_executable(self._executable):
            # Re-checked here rather than trusted from start-up, for the same
            # reason the project root is: the CLI can be updated, moved or
            # removed while the daemon is running, and the check that matters is
            # the one closest to the launch.
            raise AdapterRefusal(
                "Claude Code is no longer installed where Cofferdam found it"
            )

        with self._lock:
            self._forget_finished()
            self._forget_disowned()
            if context.task_id in self._runs:
                # A second start for one task would be a second process on one
                # session. The idempotency key in Task Core normally prevents a
                # double create; this is the structural backstop for the case it
                # does not, which is what makes "double tap launches one
                # process" true rather than likely.
                raise AdapterRefusal("this task is already running")
            active = sum(1 for run in self._runs.values() if run.poll() is None)
            if active >= self._max_concurrent:
                # Documented policy: refuse truthfully rather than queue. A
                # queued Claude task would sit holding a place in a line nobody
                # can see, and the honest sentence is short.
                raise AdapterRefusal(
                    "another Claude Code task is already running. "
                    "Cofferdam runs one at a time — wait for it or cancel it."
                )
            # The slot is claimed before the launch and released by
            # `_release` on any failure, so a launch that dies does not leave
            # the limit consumed forever.
            run = self._run_factory(
                task_id=context.task_id,
                executable=self._executable,
                project_root=context.project_root,
            )
            self._runs[context.task_id] = run

        auth = self._auth_probe(self._executable)
        if not auth.logged_in:
            self._release(context.task_id)
            return self._authentication_wait(auth)

        try:
            # The prompt goes in here rather than in a second call afterwards:
            # the CLI reports its session only once it has a turn to work on.
            launched = run.start(context.prompt)
        except Exception:
            self._release(context.task_id)
            raise AdapterRefusal("Claude Code could not be started")
        if not launched:
            reason = run.launch_error or "unknown"
            self._release(context.task_id)
            if reason == "session_mismatch":
                raise AdapterRefusal(
                    "Claude Code reported a different session than the one "
                    "Cofferdam started, so the task was not adopted"
                )
            if reason == "no_session_evidence":
                raise AdapterRefusal(
                    "Claude Code started but never reported a ready session"
                )
            if reason == "prompt_not_delivered":
                raise AdapterRefusal(
                    "the prompt could not be delivered to Claude Code"
                )
            raise AdapterRefusal("Claude Code could not be started")

        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="Claude Code started in " + context.project_id + ".",
                    detail="session ready",
                ),
            ),
            observations=(
                EvidenceReference(
                    evidence_type=EVIDENCE_PROCESS,
                    # Cofferdam launched this process and read the pid off its
                    # own `Popen`, so this genuinely is an observation. Claude
                    # was not asked and did not answer.
                    source=EVIDENCE_OS_OBSERVED,
                    identifier="pid " + str(run.pid),
                    operation="launch",
                    result="running",
                ),
            ),
            requested_state=None,
        )

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        """Deliver one more user turn to the *same* live process.

        The session is not named, looked up or supplied — it is the process this
        task already owns, found by this task's own id. There is no argument on
        this method, and no field on any request, that could point it at another
        task's run.
        """
        with self._lock:
            run = self._runs.get(context.task_id)
        if run is None or run.poll() is not None:
            raise AdapterRefusal(
                "this Claude task is no longer running, so there is nothing to answer"
            )
        if run.cancel_requested:
            raise AdapterRefusal("this task is being cancelled")
        if run.turn_in_progress() and not run.wait_for_turn(FOLLOWUP_TURN_WAIT_SECONDS):
            # Documented policy: refuse, do not queue. Writing a second user
            # message into a stream mid-turn would inject it at a point nobody
            # chose, and "it arrived somewhere in the middle" is not a
            # behaviour worth having.
            raise AdapterRefusal(
                "Claude is still working on the previous message. Try again when it stops."
            )
        if not run.send_turn(followup):
            raise AdapterRefusal("the follow-up could not be delivered to Claude Code")
        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="Your follow-up was delivered to the same Claude session.",
                ),
            )
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        """Stop this task's process, and nothing else.

        **Precedence, for the race this method is most likely to lose.** A cancel
        can arrive in the gap between a process finishing its work and Cofferdam
        noticing it: the reader thread has the ``result`` frame, the process has
        exited, and the read that would have settled the task has not happened
        yet. Reporting ``cancelled`` there would be a false claim in the
        direction that matters most to an audit — the work *was* done, and the
        record would say it was stopped.

        So a result that already arrived wins over a cancel that arrived after
        it. Task Core's graph permits ``cancelling → completed`` for exactly this
        reason, and the request is not lost: the core has already written
        ``cancellation_requested`` into the history, so what a person sees is
        that they asked and that it had already finished.

        The condition is narrow on purpose. It applies only when the process is
        **gone** and **no turn was in flight**, because a ``last_result`` left
        over from an earlier turn while a new one is running is not an answer to
        anything. That is an ordinary cancel, and it reports ``cancelled``.
        """
        with self._lock:
            run = self._runs.get(context.task_id)
        if run is None:
            raise AdapterRefusal("there is no running Claude process for this task")

        if run.poll() is not None and not run.turn_in_progress():
            with run.lock:
                finished = run.state.last_result
            if finished is not None:
                already: List[AdapterEvent] = [
                    AdapterEvent(
                        event_type=EVENT_PROGRESS,
                        text="This task had already finished when the cancel arrived.",
                    )
                ]
                return self._settle(run, finished, already, exit_code=run.exit_code)

        outcome = run.stop(reason="user_cancelled")
        run.reap()
        result = str(outcome.get("result"))
        signals = outcome.get("signals") or []
        self._release(context.task_id)

        if result == "still_running":
            # Not promoted to `cancelled`. Saying a task stopped because
            # stopping it was requested is the false success the whole design
            # refuses to produce; the core leaves it `cancelling`.
            raise AdapterRefusal("the Claude process did not stop")

        detail = ", ".join(str(item) for item in signals) if signals else result
        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="The Claude process was stopped.",
                    detail=detail,
                ),
            ),
            observations=(
                EvidenceReference(
                    evidence_type=EVIDENCE_PROCESS,
                    source=EVIDENCE_OS_OBSERVED,
                    identifier="pid " + str(run.pid),
                    operation="stop",
                    result=result,
                ),
            ),
            requested_state=STATE_CANCELLED,
        )

    # -- the asynchronous half ----------------------------------------------

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        """What has happened **since last time**, as a report the core may refuse.

        Called by Task Core on every read of a task detail. The words "since
        last time" are the whole method: an earlier version answered the same
        question with the same answer on every poll, so a task with about ten
        things to say accumulated 160 durable events, re-ran four Git
        subprocesses every ten seconds, and made the detail endpoint slow enough
        that its own responses started losing races in the panel.

        Only what changed is reported. What was reported is remembered per task
        in :attr:`_reported`, and the store refuses consecutive duplicates as
        well — two layers, because this one forgets across a restart and that
        one does not.
        """
        with self._lock:
            run = self._runs.get(context.task_id)
            seen = dict(self._reported.get(context.task_id) or {})
        if run is None:
            return AdapterOutcome()

        with run.lock:
            state = run.state
            activity = state.latest_activity
            output = state.latest_output
            result = state.last_result
            truncated = state.truncated
            oversized = state.oversized_frames

        events: List[AdapterEvent] = []
        fresh: Dict[str, object] = dict(seen)
        if activity and activity != seen.get("activity"):
            events.append(AdapterEvent(event_type=EVENT_PROGRESS, text=activity))
            fresh["activity"] = activity
        if output and output != seen.get("output"):
            events.append(
                AdapterEvent(event_type=EVENT_MEANINGFUL_OUTPUT, text=output)
            )
            fresh["output"] = output
        if truncated and not seen.get("truncated"):
            fresh["truncated"] = True
            events.append(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="Claude produced more output than Cofferdam keeps; "
                    "the rest was not recorded.",
                )
            )
        if oversized and oversized != seen.get("oversized"):
            events.append(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="Some oversized output from Claude was refused.",
                    detail=str(oversized) + " frames",
                )
            )
            fresh["oversized"] = oversized

        with self._lock:
            if context.task_id in self._runs:
                self._reported[context.task_id] = fresh

        exit_code = run.poll()

        # A refused tool, reported before anything else is concluded. This
        # adapter cannot grant the permission, so it says what was refused and
        # hands the task to a person.
        if result is not None and result.permission_denials:
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_WAITING_FOR_USER,
                waiting_reason=WAITING_APPROVAL,
                failure_message=(
                    "Claude asked to use something it is not allowed to here: "
                    + ", ".join(result.permission_denials[:4])
                    + ". Cofferdam cannot grant that from a phone."
                ),
            )

        if exit_code is None:
            # Still running. If a turn finished and nothing new was asked, the
            # task is waiting for whatever the person wants next.
            if result is not None and not run.turn_in_progress():
                return self._settle(run, result, events, exit_code=None)
            return AdapterOutcome(events=tuple(events))

        return self._settle(run, result, events, exit_code=exit_code)

    def _settle(
        self,
        run: ClaudeRun,
        result,
        events: List[AdapterEvent],
        *,
        exit_code: Optional[int],
    ) -> AdapterOutcome:
        """Decide what a finished turn or a finished process means."""
        if result is None:
            # The process ended without ever producing a result frame. Exit code
            # zero does not rescue this: something ran and reported nothing, and
            # calling that a completed task would be inventing a result.
            self._retire(run.task_id)
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_FAILED,
                failure_code="claude_no_result",
                failure_message=(
                    "Claude Code stopped without producing a result"
                    + (" (exit " + str(exit_code) + ")" if exit_code else "")
                    + "."
                ),
            )

        if result.is_error:
            # The session is finished either way: a terminal task has no use for
            # one, and this is the exact path that leaked a live process and the
            # concurrency slot in live validation.
            self._retire(run.task_id)

            if _is_authentication_failure(result.text):
                # Claude's *own* credentials expired mid-run. That is not the
                # task going wrong, and reporting it as a failure sent somebody
                # to debug a prompt when what was needed was a sign-in at the
                # workstation. Nothing of the CLI's message is kept — it can
                # name an account — only the fact and what to do about it.
                message = (
                    "Claude Code's sign-in expired while this task was running. "
                    "Sign in again on the workstation — never type a password "
                    "or code into a task."
                )
                # As an event as well as a reason. A waiting task has no
                # `failure`, so a message passed only as `failure_message` would
                # be dropped and the person would see a task waiting on
                # "authentication" with nothing telling them what to do.
                return AdapterOutcome(
                    events=tuple(events) + (
                        AdapterEvent(event_type=EVENT_PROGRESS, text=message),
                    ),
                    requested_state=STATE_WAITING_FOR_USER,
                    waiting_reason=WAITING_AUTHENTICATION,
                    failure_message=message,
                )

            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_FAILED,
                # `is_error` with subtype "success" is a real combination the
                # CLI produces, and "claude_success" is a nonsense code for a
                # failure. A contradictory subtype is not used as the code.
                failure_code=_failure_code(result.subtype),
                failure_message=result.text
                or "Claude Code reported an error without explaining it.",
            )

        if not result.text:
            # `is_error` false but nothing to show. Truthfully a failure: there
            # is no result to report, and an empty completion would look like
            # the work produced nothing when in fact nothing was reported.
            self._retire(run.task_id)
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_FAILED,
                failure_code="claude_empty_result",
                failure_message="Claude Code finished without a result to show.",
            )

        # The result text is what Claude said; it goes in an event, and any
        # evidence attached to an event is a claim by definition. What Cofferdam
        # ran for itself travels separately, through `observations`, which is
        # the only channel the core will label `git_observed`.
        #
        # Appended **once per distinct result**. `_settle` runs on every read of
        # a settled task, and re-emitting the same sentence each time is how a
        # ten-event task reached a hundred and sixty. The store also refuses
        # consecutive duplicates, but the lifecycle event that ends the turn
        # sits between two of these and stops them being consecutive — so the
        # suppression has to happen here too.
        settled_text = result.text[:MAX_RESULT_CHARS]
        with self._lock:
            already = (self._reported.get(run.task_id) or {}).get("settled")
        if settled_text != already:
            events.append(
                AdapterEvent(
                    event_type=EVENT_MEANINGFUL_OUTPUT,
                    text=settled_text,
                )
            )
            with self._lock:
                if run.task_id in self._runs:
                    self._reported.setdefault(run.task_id, {})["settled"] = settled_text
        # Cofferdam's own Git probes are four subprocesses. Running them on
        # every read of a task detail — which is what happened — is both
        # pointless and the reason the detail endpoint was slow enough to lose
        # races in the panel. They run once per distinct result.
        observations = self._observe_once(run, result.text)

        if exit_code is None:
            # Before claiming this process can take another message, give it a
            # brief moment to prove it is staying. A `result` frame is parsed
            # before stdout reaches EOF, so at this instant a process that is
            # about to exit and one that will wait for years look identical.
            #
            # This matters more than it looks. `waiting_for_user → completed` is
            # absent from Task Core's graph on purpose — receiving an answer must
            # not be able to finish a task — so a task that entered
            # `waiting_for_user` a fraction of a second before its process died
            # could never reach a terminal state again. It would sit there
            # offering a follow-up with nowhere to send it. Waiting on EOF closes
            # that window without touching the graph.
            #
            # The cost is one short wait at each turn boundary, and only there.
            # A CLI that stays open — which is the normal case, and the whole
            # reason this adapter holds one process per task — pays it once per
            # turn and nothing else.
            if run.stream_finished.wait(TURN_SETTLE_GRACE_SECONDS):
                reaped = run.reap(timeout=2.0)
                if reaped is not None:
                    exit_code = reaped

        if exit_code is None:
            # The turn finished and the process is alive. **Not**
            # `waiting_for_user`: nobody has been asked anything, and saying so
            # put "NEEDS YOU / waiting for an answer" on a phone about a task
            # that had done exactly what it was told. `ready_for_followup` says
            # the true thing — the work is done and the session is still open.
            return AdapterOutcome(
                events=tuple(events),
                observations=observations,
                requested_state=STATE_READY_FOR_FOLLOWUP,
                final_result=settled_text,
            )

        self._retire(run.task_id)
        return AdapterOutcome(
            events=tuple(events),
            observations=observations,
            requested_state=STATE_COMPLETED,
            final_result=settled_text,
        )

    def _observe_once(self, run: ClaudeRun, marker: Optional[str]):
        """Run Cofferdam's own Git probes, once per distinct turn result.

        The probes are the only thing in this adapter that can turn a claim into
        an observation, so they must run — but they are four subprocesses, and
        an earlier version ran them every time anybody opened the task. The
        marker is the result text: a new result means new work to look at, and
        the same result means the same repository state was already recorded.
        """
        key = "observed:" + (marker or "")
        with self._lock:
            if self._reported.get(run.task_id, {}).get("observed_for") == key:
                return ()
        try:
            observation = observe_git(run.project_root)
        except Exception:
            return ()
        with self._lock:
            entry = self._reported.setdefault(run.task_id, {})
            entry["observed_for"] = key
        return git_evidence(observation)

    # -- bookkeeping ---------------------------------------------------------

    def _authentication_wait(self, auth) -> AdapterOutcome:
        """The one place an authentication wait is produced.

        Nothing about the account is carried: not an address, not an
        organisation, not a token, not a file path. The message tells somebody
        what to do at the workstation, and the PWA shows no field, because there
        is nothing here for a field to submit.
        """
        if auth.probe_failed:
            message = (
                "Cofferdam could not check whether Claude Code is signed in. "
                "Check it on the workstation and start the task again."
            )
        else:
            message = (
                "Claude Code is not signed in on this workstation. Sign in "
                "there — never type a password or code into a task."
            )
        return AdapterOutcome(
            events=(
                AdapterEvent(event_type=EVENT_PROGRESS, text=message),
            ),
            requested_state=STATE_WAITING_FOR_USER,
            waiting_reason=WAITING_AUTHENTICATION,
            failure_message=message,
        )

    def release_session(self, task_id: str) -> None:
        """Close the retained session on purpose, because a person is finished.

        Closing stdin is how the CLI is told there are no more turns; it exits
        on its own, which is a cleaner ending than a signal. If it will not go,
        the escalation is the same verified one cancellation uses — and if even
        that fails, this raises, so the core leaves the task where it is rather
        than recording a completion over a process that is still running.
        """
        with self._lock:
            run = self._runs.get(task_id)
        if run is None:
            return
        run.close_input()
        if run.reap(timeout=5.0) is None:
            outcome = run.stop(reason="session_finished")
            run.reap(timeout=2.0)
            if str(outcome.get("result")) == "still_running":
                raise AdapterRefusal("the Claude process did not close")
        self._release(task_id)

    def _release(self, task_id: str) -> None:
        """Drop the bookkeeping for a run whose process is already gone.

        Used where the process never started, or has been observed to stop. It
        deliberately declines to drop an entry whose process is still alive:
        forgetting a live process would mean the adapter no longer knows about
        something it started, which is worse than holding the slot.

        **This is not the right call for a task that has just become terminal.**
        Use :meth:`_retire`, which ends the process first.
        """
        with self._lock:
            run = self._runs.get(task_id)
            if run is None:
                return
            if run.poll() is None and run.still_ours():
                return
            self._runs.pop(task_id, None)
            self._reported.pop(task_id, None)

    def _retire(self, task_id: str) -> None:
        """End the process for a task that has reached a terminal state.

        The bug this exists to fix, seen in live validation: a task failed while
        its process was still alive — Claude's own OAuth token expired mid-run,
        the CLI reported the error and went on waiting for input — and
        :meth:`_release` declined to drop a live run. So a *finished* task kept
        its process and kept the single concurrency slot, and every task created
        afterwards was refused with "another Claude Code task is already
        running". Six in a row were, and nothing short of a daemon restart would
        have freed it.

        A terminal task has no use for a session. Closing stdin is the gentle
        way out and the CLI exits on its own; if it does not, the same
        identity-verified escalation cancellation uses applies. The entry is
        dropped either way — an adapter that cannot forget a run it can no
        longer act on is an adapter that blocks the machine forever.
        """
        with self._lock:
            run = self._runs.get(task_id)
        if run is None:
            return
        try:
            run.close_input()
            if run.reap(timeout=3.0) is None:
                run.stop(reason="task_finished")
                run.reap(timeout=2.0)
        except Exception:
            # Never let tidying up fail a task that already has its answer.
            pass
        with self._lock:
            self._runs.pop(task_id, None)
            self._reported.pop(task_id, None)

    def _forget_finished(self) -> None:
        """Drop runs whose process has exited. Called before every launch.

        The caller holds the lock.
        """
        for task_id in [
            key for key, run in self._runs.items() if run.poll() is not None
        ]:
            self._runs.pop(task_id, None)
            self._reported.pop(task_id, None)

    def _forget_disowned(self) -> None:
        """Drop runs the adapter can no longer act on. Called before every launch.

        The backstop for the failure this class has already had once: an entry
        whose process is gone in a way ``poll()`` cannot see — a pid that is no
        longer ours because it exited and was reused, or a run whose identity
        stopped matching. Such an entry occupies the concurrency slot and can
        never be freed by anything, because every path that would free it starts
        by finding a process that is not there.

        Holding the slot for a process the adapter still owns is correct.
        Holding it for one it demonstrably does not is a machine that refuses
        work until it is restarted.

        The caller holds the lock. Nothing is signalled here: a run that is not
        ours is precisely the one never to send a signal to.
        """
        for task_id in [
            key
            for key, run in self._runs.items()
            if run.poll() is None and not run.still_ours()
        ]:
            self._runs.pop(task_id, None)
            self._reported.pop(task_id, None)

    def active_task_ids(self):
        with self._lock:
            return tuple(
                task_id
                for task_id, run in self._runs.items()
                if run.poll() is None
            )

    def shutdown(self) -> None:
        """Stop every run this adapter owns. Called when the service stops."""
        with self._lock:
            runs = list(self._runs.values())
            self._runs.clear()
        for run in runs:
            try:
                run.stop(reason="service_stopping")
                run.reap(timeout=2.0)
            except Exception:
                continue


__all__ = [
    "ADAPTER_ID",
    "DEFAULT_MAX_CONCURRENT_TASKS",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "LIMITATIONS",
    "ClaudeCodeAdapter",
]
