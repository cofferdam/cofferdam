"""The validation task adapter: a lifecycle to exercise, and nothing else.

This exists so the task lifecycle can be validated end to end on a real host —
created through completed, waiting through follow-up, cancel, and a daemon
restart — **without invoking Claude, any other model, or any process at all**.

It is not an agent. It is not AI. It does nothing a person asked for. Given a
prompt it emits a fixed, code-owned sequence of events and reaches a fixed
terminal state. The prompt is carried so the storage, bounds and privacy paths
are exercised with real content; it is never interpreted, and the only thing the
adapter ever does with it is note its length.

What it cannot do, structurally
-------------------------------

There is no import of :mod:`subprocess`, :mod:`os`, :mod:`socket`,
:mod:`shutil`, :mod:`pathlib` writes, or any network client in this file, and no
call that could reach one. It does not read the project root — it is *given* one
and ignores it. It writes nothing to disk: everything it "remembers" lives in a
dictionary in memory, and it goes away with the process, which is also what
makes the restart-interruption scenario real rather than simulated.

That is asserted by tests rather than trusted, because "the file currently has
no dangerous import" is a property that survives exactly as long as nobody adds
one.

How it is enabled
-----------------

Never by default, and never by a client. The server-side configuration decides —
``--enable-validation-task-adapter`` on the command line, or
``enable_validation_task_adapter`` in ``config.json``, or
``COFFERDAM_ENABLE_VALIDATION_TASK_ADAPTER=1``. A phone has no field that
reaches this, and there is no route that turns it on. When it is off, the
adapter is not registered at all: it does not appear in the adapter list, and
naming it in a task creation request is an unknown-adapter refusal.

Scenarios
---------

Chosen from a **code-owned** table, selected by an optional bounded keyword the
prompt may begin with. A client cannot supply delays, event scripts, or arbitrary
behaviour — only pick one of five things this file already knows how to do.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from ..models import (
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_PROGRESS,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    EVENT_WAITING_FOR_USER,
    EVIDENCE_ADAPTER_REPORTED,
    EVIDENCE_ARTIFACT,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_WAITING_FOR_USER,
    WAITING_CLARIFICATION,
    EvidenceReference,
)
from .protocol import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterOutcome,
    AdapterRefusal,
    TaskAdapter,
    TaskContext,
)

ADAPTER_ID = "validation"

#: What a person sees, everywhere. Deliberately not "Claude", not "AI", not
#: "agent": somebody looking at this in the PWA must not be able to mistake it
#: for something that did real work.
DISPLAY_NAME = "Validation task adapter"

DESCRIPTION = (
    "A deterministic test adapter. It runs no program, calls no model and "
    "changes nothing — it only exercises the task lifecycle."
)

# -- the code-owned scenario table -------------------------------------------

SCENARIO_COMPLETE = "complete"
SCENARIO_WAIT = "wait"
SCENARIO_FAIL = "fail"
SCENARIO_CANCEL = "cancel"
SCENARIO_INTERRUPT = "interrupt"

SCENARIOS: Tuple[str, ...] = (
    SCENARIO_COMPLETE,
    SCENARIO_WAIT,
    SCENARIO_FAIL,
    SCENARIO_CANCEL,
    SCENARIO_INTERRUPT,
)

DEFAULT_SCENARIO = SCENARIO_COMPLETE

#: How a scenario is chosen: the prompt may *begin* with one of these words.
#: This is the entire client influence over adapter behaviour — five words from
#: a fixed tuple, matched exactly, with everything else ignored. There is no
#: field for a delay, a step count, an event list or a failure message, because
#: a validation adapter that accepted a script would be a general-purpose
#: event injector wearing a test's clothes.
SCENARIO_PREFIX = "scenario:"

#: Descriptions the PWA shows beside the picker, so somebody choosing one knows
#: what it will do before they press start.
SCENARIO_DESCRIPTIONS: Dict[str, str] = {
    SCENARIO_COMPLETE: "Runs through a few progress steps and completes with a result.",
    SCENARIO_WAIT: "Runs, then waits for one follow-up, then completes.",
    SCENARIO_FAIL: "Runs, then fails with a synthetic error.",
    SCENARIO_CANCEL: "Stays running until it is cancelled.",
    SCENARIO_INTERRUPT: "Stays running so the service can be restarted under it.",
}


def scenario_for(prompt: str) -> str:
    """Which scenario a prompt selects. Never raises; unknown means default.

    Deliberately forgiving, because this is a validation tool and a typo should
    run the ordinary scenario rather than produce a refusal that teaches nothing
    about the lifecycle.
    """
    text = (prompt or "").strip().lower()
    if not text.startswith(SCENARIO_PREFIX):
        return DEFAULT_SCENARIO
    candidate = text[len(SCENARIO_PREFIX) :].strip().split()
    if not candidate:
        return DEFAULT_SCENARIO
    return candidate[0] if candidate[0] in SCENARIOS else DEFAULT_SCENARIO


class ValidationTaskAdapter(TaskAdapter):
    """A lifecycle exerciser with no capabilities beyond talking about itself.

    Stateful across calls within one process — it remembers which tasks it has
    "started" and whether a follow-up has arrived — and that state is a plain
    dictionary that dies with the daemon. That is the point: after a restart it
    has no memory of a running task, which is exactly the situation restart
    recovery has to handle honestly.
    """

    adapter_id = ADAPTER_ID
    display_name = DISPLAY_NAME
    description = DESCRIPTION

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: task_id -> scenario. Memory only, and gone on restart by design.
        self._started: Dict[str, str] = {}
        self._followed_up: Dict[str, bool] = {}

    # -- declaration ---------------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            start=True,
            followup=True,
            cancel=True,
            # False, and load-bearing: this adapter genuinely cannot reattach to
            # a task after a restart, so it says so, and Task Core marks such
            # tasks interrupted rather than offering a recovery that would be a
            # lie. An adapter that claimed this would be claiming memory it does
            # not have.
            recover_after_restart=False,
            structured_progress=True,
            final_result=True,
            approvals=False,
            authentication_waits=False,
        )

    def available(self) -> bool:
        # Registration is the gate. If this object exists in the registry, the
        # server was explicitly configured to allow it, so there is nothing
        # further to check — and nothing here reads a client value.
        return True

    def describe(self) -> Dict[str, object]:
        payload = super().describe()
        payload["validation_only"] = True
        payload["scenarios"] = [
            {"scenario": name, "description": SCENARIO_DESCRIPTIONS[name]}
            for name in SCENARIOS
        ]
        return payload

    # -- lifecycle -----------------------------------------------------------

    def start(self, context: TaskContext) -> AdapterOutcome:
        scenario = scenario_for(context.prompt)
        with self._lock:
            self._started[context.task_id] = scenario
            self._followed_up.setdefault(context.task_id, False)

        events: List[AdapterEvent] = [
            AdapterEvent(
                event_type=EVENT_PROGRESS,
                text="Validation adapter started. No program is being run.",
            ),
            AdapterEvent(
                event_type=EVENT_PROGRESS,
                # The prompt's *length*, never its text. The one thing this
                # adapter says about the content is that it received some, which
                # is enough to prove the storage path carried it.
                text="Received a prompt of " + str(len(context.prompt)) + " characters.",
            ),
        ]

        if scenario == SCENARIO_COMPLETE:
            events.extend(
                [
                    AdapterEvent(
                        event_type=EVENT_MEANINGFUL_OUTPUT,
                        text="Step 1 of 2: checked the task context.",
                    ),
                    AdapterEvent(
                        event_type=EVENT_MEANINGFUL_OUTPUT,
                        text="Step 2 of 2: produced a synthetic result.",
                        evidence=(
                            EvidenceReference(
                                evidence_type=EVIDENCE_ARTIFACT,
                                # Labelled as a claim, because that is what it
                                # is. Nothing observed this; the adapter said it.
                                source=EVIDENCE_ADAPTER_REPORTED,
                                identifier="validation-artifact-1",
                                operation="produced",
                                result="synthetic",
                            ),
                        ),
                    ),
                    AdapterEvent(
                        event_type=EVENT_TASK_COMPLETED,
                        text="Validation scenario finished.",
                    ),
                ]
            )
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_COMPLETED,
                final_result=_complete_result(context, followed_up=False),
            )

        if scenario == SCENARIO_WAIT:
            events.append(
                AdapterEvent(
                    event_type=EVENT_WAITING_FOR_USER,
                    text="Waiting for one follow-up message before finishing.",
                )
            )
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_WAITING_FOR_USER,
                waiting_reason=WAITING_CLARIFICATION,
            )

        if scenario == SCENARIO_FAIL:
            events.append(
                AdapterEvent(
                    event_type=EVENT_TASK_FAILED,
                    text="Validation scenario failed on purpose.",
                )
            )
            return AdapterOutcome(
                events=tuple(events),
                requested_state=STATE_FAILED,
                failure_code="validation_scenario_failed",
                failure_message="The validation scenario failed on purpose. Nothing was run.",
            )

        # cancel and interrupt: stay running, and wait to be acted on. Neither
        # spawns a timer or a thread — "still running" here means "no further
        # report has been made", which is exactly what it means for a real agent.
        events.append(
            AdapterEvent(
                event_type=EVENT_MEANINGFUL_OUTPUT,
                text=(
                    "Running until cancelled."
                    if scenario == SCENARIO_CANCEL
                    else "Running until the service is restarted."
                ),
            )
        )
        return AdapterOutcome(events=tuple(events), requested_state=STATE_RUNNING)

    def send_followup(self, context: TaskContext, followup: str) -> AdapterOutcome:
        with self._lock:
            if context.task_id not in self._started:
                # No memory of this task: the daemon restarted under it. Saying
                # so is better than pretending to resume something this process
                # never started.
                raise AdapterRefusal(
                    "the validation adapter has no memory of that task",
                    "it was started before the service restarted",
                )
            self._followed_up[context.task_id] = True

        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    # The follow-up's length, not its text — the same rule as the
                    # prompt. The content is stored on the task and shown in the
                    # detail view; it does not need to be repeated into an event.
                    text="Follow-up received (" + str(len(followup)) + " characters). Resuming.",
                ),
                AdapterEvent(
                    event_type=EVENT_TASK_COMPLETED,
                    text="Validation scenario finished after the follow-up.",
                ),
            ),
            requested_state=STATE_COMPLETED,
            final_result=_complete_result(context, followed_up=True),
        )

    def cancel(self, context: TaskContext) -> AdapterOutcome:
        with self._lock:
            known = context.task_id in self._started
            self._started.pop(context.task_id, None)
            self._followed_up.pop(context.task_id, None)

        if not known:
            # Truthful rather than convenient: this process is not running that
            # task, so it cannot claim to have stopped it. Task Core decides what
            # to do with a refusal; the adapter's job is not to smooth it over.
            raise AdapterRefusal(
                "the validation adapter is not running that task",
                "it may have been started before the service restarted",
            )

        return AdapterOutcome(
            events=(
                AdapterEvent(
                    event_type=EVENT_PROGRESS,
                    text="Validation adapter stopped the scenario.",
                ),
            ),
            requested_state=STATE_CANCELLED,
        )

    def inspect(self, context: TaskContext) -> AdapterOutcome:
        with self._lock:
            known = context.task_id in self._started
        if known:
            return AdapterOutcome()
        return AdapterOutcome(accepted=False)

    # -- test/validation support --------------------------------------------

    def forget_all(self) -> None:
        """Drop every in-memory task, as a restart would.

        Exists so the restart scenario can be exercised in a test without
        restarting a real daemon. It removes memory; it never writes a state,
        because deciding what an interrupted task becomes is Task Core's.
        """
        with self._lock:
            self._started.clear()
            self._followed_up.clear()

    def knows(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._started


def _complete_result(context: TaskContext, *, followed_up: bool) -> str:
    """The synthetic final result.

    Says what happened and — deliberately — repeats none of the prompt or the
    follow-up back. Acknowledging that a follow-up arrived is useful; echoing it
    would put the same private text in a second place for no gain.
    """
    lines = [
        "Validation scenario completed.",
        "",
        "This adapter ran no program, called no model and changed nothing on this",
        "machine. It exists to prove the task lifecycle works end to end.",
        "",
        "project: " + context.project_id,
        "task: " + context.task_id,
    ]
    if followed_up:
        lines.append("A follow-up was received and the task resumed after it.")
    return "\n".join(lines)


__all__ = [
    "ADAPTER_ID",
    "DEFAULT_SCENARIO",
    "DESCRIPTION",
    "DISPLAY_NAME",
    "SCENARIOS",
    "SCENARIO_CANCEL",
    "SCENARIO_COMPLETE",
    "SCENARIO_DESCRIPTIONS",
    "SCENARIO_FAIL",
    "SCENARIO_INTERRUPT",
    "SCENARIO_PREFIX",
    "SCENARIO_WAIT",
    "ValidationTaskAdapter",
    "scenario_for",
]
