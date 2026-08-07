"""Task Core's orchestration: create, start, follow up, cancel, recover.

Everything a route does goes through this class, and this class does five things
in the same order every time:

1. **Resolve authority** — the project from the host registry, the adapter from
   the code-owned table, the root re-verified on disk. Nothing a client sent is
   ever a destination.
2. **Validate content** against bounds, refusing rather than truncating.
3. **Write durably first.** The task exists before the adapter is called, so a
   crash between the two leaves a task that can be reported as interrupted
   rather than work that happened with no record of it.
4. **Ask the adapter** and take its answer as a *report*.
5. **Apply that report through the transition graph**, which may refuse it.

Restart recovery
----------------

:meth:`TaskService.recover_after_restart` runs once at start-up, and its rule is
the whole reason this milestone exists before any real agent:

    A row that says ``running`` is not evidence that anything is running.

The process that was running it is gone. Nothing is resumed — not because
resuming is hard, but because resuming something whose state nobody observed is
how a task system starts lying. Every non-terminal task becomes ``interrupted``,
with an event that says so, and its previous output is left exactly as it was.
Terminal tasks are not touched at all.

The one nuance is ``recover_after_restart``: an adapter that genuinely can
reattach gets ``recovery_required`` instead, which is non-terminal and waits for
a decision this milestone does not implement. No adapter declares it today, so
in practice every interrupted task is ``interrupted`` — and that is stated in
the guide rather than left for someone to discover.
"""

from __future__ import annotations

import hashlib
import threading
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..runtime.identity import now_iso
from .adapters import AdapterRegistry, AdapterRefusal, TaskAdapter, TaskContext
from .errors import (
    AdapterFailed,
    AdapterNotPermitted,
    CancelUnsupported,
    FollowupInvalid,
    FollowupNotWaiting,
    FollowupUnsupported,
    PromptInvalid,
    RequestIdInvalid,
    TaskAlreadyFinished,
    TaskError,
    TaskUnknown,
)
from .identity import valid_task_id
from .lifecycle import IllegalTransition, can_transition
from .models import (
    ACTOR_SYSTEM,
    ACTOR_USER,
    CORE_OWNED_EVENT_TYPES,
    EVENT_ACTION_REJECTED,
    EVENT_ADAPTER_STARTING,
    EVENT_CANCELLATION_REQUESTED,
    EVENT_FOLLOWUP_RECEIVED,
    EVENT_MEANINGFUL_OUTPUT,
    EVENT_TASK_CANCELLED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    EVENT_TASK_INTERRUPTED,
    EVENT_TASK_QUEUED,
    EVENT_TASK_STARTED,
    EVENT_TURN_COMPLETE,
    EVENT_WAITING_FOR_USER,
    EVENT_PROGRESS,
    EVIDENCE_ADAPTER_REPORTED,
    MAX_CLIENT_REQUEST_ID_CHARS,
    MAX_EVIDENCE_ITEMS,
    MAX_FOLLOWUP_CHARS,
    MAX_LABEL_CHARS,
    MAX_PROMPT_CHARS,
    ORIGIN_PWA,
    SOURCE_COFFERDAM,
    SOURCE_RESTART_RECOVERY,
    STATE_CANCELLED,
    STATE_CANCELLING,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_QUEUED,
    STATE_READY_FOR_FOLLOWUP,
    STATE_RECOVERY_REQUIRED,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_WAITING_FOR_USER,
    TERMINAL_STATES,
    VERIFIED_EVIDENCE_SOURCES,
    WAITING_REASONS,
    WAITING_UNKNOWN,
    EvidenceReference,
    TaskFailure,
    TaskSnapshot,
    bounded_line,
    valid_user_text,
)
from .projects import ProjectRegistry, load_projects, verify_root
from .store import TaskRow, TaskStore

#: Audit hook signature: ``(operation, result, task_id, adapter_id, project_id,
#: correlation_id)``. Content never travels through it — see
#: :meth:`TaskService._audit`.
AuditHook = Callable[[str, str, Optional[str], Optional[str], Optional[str], Optional[str]], None]

#: The two non-terminal states in which a follow-up is a sensible thing to send.
#:
#: They are different sentences and must stay different — ``waiting_for_user``
#: means somebody was asked something, ``ready_for_followup`` means a turn ended
#: and the session is still open — but from the service's point of view both are
#: "the task can take another message from a person".
FOLLOWUP_STATES = frozenset({STATE_WAITING_FOR_USER, STATE_READY_FOR_FOLLOWUP})


def _payload_hash(*parts: object) -> str:
    """A stable digest of a request's meaningful fields.

    Used only to answer "is this the same request as the one that used this key
    before". It is a hash of user content, so it is never published, never
    logged and never leaves the database — it exists to compare, not to describe.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(b"\x1f")
        digest.update(unicodedata.normalize("NFC", str(part)).encode("utf-8"))
    return digest.hexdigest()


class TaskService:
    """The one place tasks are created, advanced and ended."""

    def __init__(
        self,
        config,
        store: TaskStore,
        adapters: AdapterRegistry,
        *,
        projects: Optional[ProjectRegistry] = None,
        audit: Optional[AuditHook] = None,
    ) -> None:
        self._config = config
        self._store = store
        self._adapters = adapters
        self._projects = projects if projects is not None else load_projects(
            config, adapters.ids()
        )
        self._audit_hook = audit
        #: One task advanced at a time. The store is transactional on its own;
        #: this serialises the *adapter* calls so a cancel and a follow-up for
        #: the same task cannot both be mid-flight against different states.
        self._lock = threading.RLock()

    # -- accessors -----------------------------------------------------------

    @property
    def store(self) -> TaskStore:
        return self._store

    @property
    def adapters(self) -> AdapterRegistry:
        return self._adapters

    @property
    def projects(self) -> ProjectRegistry:
        return self._projects

    def reload_projects(self) -> ProjectRegistry:
        self._projects = load_projects(self._config, self._adapters.ids())
        return self._projects

    def _audit(
        self,
        operation: str,
        result: str,
        *,
        task_id: Optional[str] = None,
        adapter_id: Optional[str] = None,
        project_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Record one bounded operational fact.

        The signature is the boundary: there is no parameter for a prompt, a
        follow-up or a result, so content cannot reach the audit path by
        accident. Everything here is either an id Cofferdam minted or a word
        from a closed vocabulary.
        """
        if self._audit_hook is None:
            return
        try:
            self._audit_hook(operation, result, task_id, adapter_id, project_id, correlation_id)
        except Exception:  # pragma: no cover - auditing must never fail an action
            pass

    # -- snapshots -----------------------------------------------------------

    def snapshot(self, row: TaskRow) -> TaskSnapshot:
        adapter = self._adapters.find(row.adapter_id)
        project = None
        for candidate in self._projects.projects:
            if candidate.project_id == row.project_id:
                project = candidate
                break
        return TaskSnapshot(
            task_id=row.task_id,
            correlation_id=row.correlation_id,
            parent_task_id=row.parent_task_id,
            origin=row.origin,
            adapter_id=row.adapter_id,
            adapter_display_name=adapter.display_name if adapter else None,
            project_id=row.project_id,
            project_display_name=project.display_name if project else None,
            state=row.state,
            waiting_reason=row.waiting_reason,
            lifecycle_revision=row.lifecycle_revision,
            created_at=row.created_at,
            started_at=row.started_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
            title=row.title,
            latest_activity=row.latest_activity,
            latest_meaningful_output=row.latest_output,
            final_result=row.final_result,
            failure=row.failure,
            cancellation=row.cancellation,
            adapter_capabilities=(
                adapter.capabilities().to_dict() if adapter else {}
            ),
            event_cursor=row.event_cursor,
            # A count, not a list. The full resource audit is a later milestone;
            # what this milestone can honestly say is how much evidence exists.
            resource_summary={"evidence_reported": 0},
        )

    def get_task(self, task_id: object) -> TaskRow:
        if not valid_task_id(task_id):
            raise TaskUnknown()
        return self._store.get(task_id)

    def refresh_task(self, task_id: object) -> TaskRow:
        """Ask a task's adapter what has happened, then apply it. Read path only.

        The foundation declared :meth:`~.adapters.protocol.TaskAdapter.inspect`
        and never called it, because the only adapter it shipped was synchronous
        — a validation adapter finishes inside ``start``, so there was never
        anything to ask about afterwards. A real agent is not like that: its
        work happens over minutes, in a process, while nothing is calling the
        adapter at all.

        This is that missing call, and it is deliberately the *whole* mechanism
        rather than one of several. There is no callback from an adapter into
        the service, no work queue, no background sweep — each of those would be
        a second path that writes task state, and the most valuable property of
        this package is that :meth:`_apply` is the only one.

        An adapter with nothing to say returns an empty outcome and nothing
        happens. What it does say goes through the same transition graph as
        every other report, so a Claude adapter cannot move a task anywhere the
        validation adapter could not.
        """
        row = self.get_task(task_id)
        if row.state in TERMINAL_STATES:
            # Never ask about a finished task. Its history is closed, and an
            # adapter that answered would be answering about a record that is
            # not open to change.
            return row
        adapter = self._adapters.find(row.adapter_id)
        if adapter is None or not adapter.capabilities().structured_progress:
            return row

        try:
            project = self._projects.get(row.project_id)
            root = verify_root(project.root)
        except TaskError:
            return row

        with self._lock:
            row = self._store.get(row.task_id)
            if row.state in TERMINAL_STATES:
                return row
            context = self._context(row, root, adapter)
            try:
                outcome = adapter.inspect(context)
            except AdapterRefusal:
                # "Nothing to report" must never be able to fail a task
                # somebody is merely looking at.
                return row
            except Exception as exc:
                return self._fail(
                    row,
                    "task_adapter_error",
                    "the task adapter stopped unexpectedly",
                    type(exc).__name__,
                )
            if not outcome.events and outcome.requested_state is None:
                return row
            return self._apply(
                row, outcome, cancelling=row.state == STATE_CANCELLING
            )

    def list_tasks(self, *, bucket: Optional[str] = None, limit: int = 50) -> List[TaskRow]:
        from .models import ACTIVE_STATES, BUCKET_ACTIVE, BUCKET_FINISHED, BUCKET_WAITING

        if bucket == BUCKET_ACTIVE:
            states: Optional[Sequence[str]] = sorted(ACTIVE_STATES)
        elif bucket == BUCKET_WAITING:
            states = [STATE_WAITING_FOR_USER, STATE_RECOVERY_REQUIRED]
        elif bucket == BUCKET_FINISHED:
            states = sorted(TERMINAL_STATES)
        else:
            states = None
        return self._store.list_tasks(states=states, limit=limit)

    # -- creating ------------------------------------------------------------

    def create_task(
        self,
        *,
        project_id: object,
        adapter_id: object,
        prompt: object,
        client_request_id: object = None,
        origin: str = ORIGIN_PWA,
        title: object = None,
    ) -> Tuple[TaskRow, bool]:
        """Create a task and run its adapter's start. Returns ``(task, created)``.

        ``origin`` is a *parameter of this method*, supplied by the route from
        the authenticated request context. It is not read from a body anywhere
        in this file, and there is no field for it in the request schema.
        """
        project, adapter = self._resolve(project_id, adapter_id)
        text = self._valid_prompt(prompt)
        request_key = self._valid_request_id(client_request_id)
        label = bounded_line(title, MAX_LABEL_CHARS)

        # Re-verified now, not at load: a directory can be deleted or replaced
        # between service start and this moment, and the check that matters is
        # the one closest to the work.
        root = verify_root(project.root)

        row, created = self._store.create_task(
            origin=origin,
            adapter_id=adapter.adapter_id,
            project_id=project.project_id,
            prompt=text,
            title=label,
            request_key=request_key,
            payload_hash=_payload_hash(project.project_id, adapter.adapter_id, text)
            if request_key
            else None,
        )
        if not created:
            # A retry of a request that already produced a task. The task is
            # returned unchanged and the adapter is *not* started again — which
            # is the entire point of the key.
            self._audit(
                "task_create",
                "duplicate",
                task_id=row.task_id,
                adapter_id=row.adapter_id,
                project_id=row.project_id,
                correlation_id=row.correlation_id,
            )
            return row, False

        self._audit(
            "task_create",
            "ok",
            task_id=row.task_id,
            adapter_id=row.adapter_id,
            project_id=row.project_id,
            correlation_id=row.correlation_id,
        )
        return self._start(row, adapter, root), True

    def _start(self, row: TaskRow, adapter: TaskAdapter, root: Path) -> TaskRow:
        """Queue, start, and apply whatever the adapter reports.

        Each step is its own durable transition, so a crash anywhere in here
        leaves a task in a state that describes how far it actually got.
        """
        with self._lock:
            row = self._store.transition(
                row.task_id,
                STATE_QUEUED,
                event_type=EVENT_TASK_QUEUED,
                actor=ACTOR_SYSTEM,
                source=SOURCE_COFFERDAM,
                expected_state=row.state,
            )
            row = self._store.transition(
                row.task_id,
                STATE_STARTING,
                event_type=EVENT_ADAPTER_STARTING,
                actor=ACTOR_SYSTEM,
                source=SOURCE_COFFERDAM,
                expected_state=STATE_QUEUED,
                text="Handing the task to the " + adapter.display_name + ".",
            )
            self._audit(
                "task_start",
                "requested",
                task_id=row.task_id,
                adapter_id=row.adapter_id,
                project_id=row.project_id,
                correlation_id=row.correlation_id,
            )

            context = self._context(row, root, adapter)
            try:
                outcome = adapter.start(context)
            except AdapterRefusal as refusal:
                return self._fail(row, "task_adapter_refused", refusal.message, refusal.detail)
            except Exception as exc:  # an adapter fault must not take the service down
                return self._fail(
                    row,
                    "task_adapter_error",
                    "the task adapter stopped unexpectedly",
                    type(exc).__name__,
                )

            # Every start passes through running, even when the adapter finished
            # immediately: skipping it would produce a task that was never
            # observed running and a history with a hole where the work went.
            row = self._store.transition(
                row.task_id,
                STATE_RUNNING,
                event_type=EVENT_TASK_STARTED,
                actor=ACTOR_SYSTEM,
                source=SOURCE_COFFERDAM,
                expected_state=STATE_STARTING,
            )
            return self._apply(row, outcome)

    # -- follow-up -----------------------------------------------------------

    def send_followup(
        self,
        task_id: object,
        followup: object,
        *,
        client_request_id: object = None,
    ) -> TaskRow:
        """Deliver one answer to a task that is waiting for one."""
        row = self.get_task(task_id)
        text = self._valid_followup(followup)
        adapter = self._adapters.get(row.adapter_id)
        request_key = self._valid_request_id(client_request_id)

        # The idempotency check comes **before** the state checks, and the order
        # is the whole point. A retry of an answer that was already delivered
        # arrives at a task that has since moved on — usually to `completed`,
        # because delivering the answer is what completed it. Checking the state
        # first would turn a successful retry into "that task is already
        # finished", which is true and useless: the phone would show a refusal
        # for a message that did in fact get through.
        if request_key is not None:
            seen = self._store.remember_followup(
                row.task_id,
                "task_followup:" + row.task_id,
                request_key,
                _payload_hash(row.task_id, text),
            )
            if seen is not None:
                # Already delivered. Sending it again would mean an adapter
                # received the same answer twice for one question, which is not
                # something a dropped connection should be able to cause.
                self._audit(
                    "task_followup",
                    "duplicate",
                    task_id=row.task_id,
                    adapter_id=row.adapter_id,
                    project_id=row.project_id,
                    correlation_id=row.correlation_id,
                )
                return self._store.get(row.task_id)

        if not adapter.capabilities().followup:
            self._reject(row, "followup", "adapter does not support follow-up")
            raise FollowupUnsupported(row.adapter_id)
        if row.state in TERMINAL_STATES:
            self._reject(row, "followup", "task is " + row.state)
            raise TaskAlreadyFinished(row.state)
        if row.state not in FOLLOWUP_STATES:
            self._reject(row, "followup", "task is " + row.state)
            raise FollowupNotWaiting(row.state)

        project = self._projects.get(row.project_id)
        root = verify_root(project.root)

        with self._lock:
            # Re-read inside the lock: the task may have finished while this
            # request was being validated, and a follow-up applied to a
            # completed task would be a write against a closed history.
            row = self._store.get(row.task_id)
            if row.state not in FOLLOWUP_STATES:
                raise FollowupNotWaiting(row.state)
            previous = row.state

            row = self._store.transition(
                row.task_id,
                STATE_RUNNING,
                event_type=EVENT_FOLLOWUP_RECEIVED,
                actor=ACTOR_USER,
                source=SOURCE_COFFERDAM,
                expected_state=previous,
                # The event says one arrived and how long it was. The text
                # itself is user content: it lives on the task, is shown in the
                # detail view, and is not copied into the history.
                text="Follow-up received (" + str(len(text)) + " characters).",
            )
            self._audit(
                "task_followup",
                "ok",
                task_id=row.task_id,
                adapter_id=row.adapter_id,
                project_id=row.project_id,
                correlation_id=row.correlation_id,
            )

            context = self._context(row, root, adapter, followup=text)
            try:
                outcome = adapter.send_followup(context, text)
            except AdapterRefusal as refusal:
                return self._fail(row, "task_adapter_refused", refusal.message, refusal.detail)
            except Exception as exc:
                return self._fail(
                    row,
                    "task_adapter_error",
                    "the task adapter stopped unexpectedly",
                    type(exc).__name__,
                )
            return self._apply(row, outcome)

    def finish_task(self, task_id: object) -> TaskRow:
        """Close a retained session on purpose, and complete the task.

        The way out of a successfully finished turn that is not cancellation.
        Cancelling a task whose work is done would record it as stopped, which
        is false and is the sort of falsehood an audit is least able to
        recover from — so a person who is simply finished says so, and the task
        completes with the result it already produced.

        Valid only from :data:`~.models.STATE_READY_FOR_FOLLOWUP`. A task that
        is *waiting for an answer* cannot be finished this way: something was
        asked and pretending otherwise would lose the question.
        """
        row = self.get_task(task_id)
        if row.state in TERMINAL_STATES:
            self._reject(row, "finish", "task is " + row.state)
            raise TaskAlreadyFinished(row.state)
        if row.state != STATE_READY_FOR_FOLLOWUP:
            self._reject(row, "finish", "task is " + row.state)
            raise FollowupNotWaiting(row.state)

        adapter = self._adapters.get(row.adapter_id)

        with self._lock:
            row = self._store.get(row.task_id)
            if row.state != STATE_READY_FOR_FOLLOWUP:
                raise FollowupNotWaiting(row.state)

            # Tell the adapter first. If it cannot let go of the session, the
            # task stays where it is rather than being marked complete over
            # something that is still running.
            release = getattr(adapter, "release_session", None)
            if callable(release):
                try:
                    release(row.task_id)
                except AdapterRefusal as refusal:
                    self._reject(row, "finish", refusal.message)
                    raise
                except Exception as exc:
                    return self._fail(
                        row,
                        "task_adapter_error",
                        "the task adapter stopped unexpectedly",
                        type(exc).__name__,
                    )

            row = self._store.transition(
                row.task_id,
                STATE_COMPLETED,
                event_type=EVENT_TASK_COMPLETED,
                actor=ACTOR_USER,
                source=SOURCE_COFFERDAM,
                expected_state=STATE_READY_FOR_FOLLOWUP,
                text="Finished by you. The adapter's session was closed.",
            )
            self._audit(
                "task_finish",
                "ok",
                task_id=row.task_id,
                adapter_id=row.adapter_id,
                project_id=row.project_id,
                correlation_id=row.correlation_id,
            )
            return row

    # -- cancellation --------------------------------------------------------

    def cancel_task(self, task_id: object) -> TaskRow:
        """Ask one task's own adapter to stop it. Nothing broader ever happens.

        Cancellation here is a message to the adapter that owns *this* task. It
        is not a signal, not a kill, not a process lookup, and it cannot reach
        another task: the only identifier that leaves this method is the task's
        own context. There is no code path in Task Core that enumerates
        processes or matches them by name, which is what makes "cancel cannot
        affect an unrelated task" structural.
        """
        row = self.get_task(task_id)

        if row.state in TERMINAL_STATES:
            # Documented convention: cancelling something already finished is a
            # truthful refusal rather than a silent success, because a client
            # that gets "ok" learns the task changed when it did not.
            self._reject(row, "cancel", "task is " + row.state)
            raise TaskAlreadyFinished(row.state)

        adapter = self._adapters.get(row.adapter_id)
        if not adapter.capabilities().cancel:
            self._reject(row, "cancel", "adapter does not support cancel")
            raise CancelUnsupported(row.adapter_id)

        with self._lock:
            row = self._store.get(row.task_id)
            if row.state in TERMINAL_STATES:
                raise TaskAlreadyFinished(row.state)
            if row.state == STATE_CANCELLING:
                # Already asked. Repeating it is harmless and must not corrupt
                # anything, so the current state is returned unchanged rather
                # than transitioned a second time.
                self._audit(
                    "task_cancel",
                    "already_requested",
                    task_id=row.task_id,
                    adapter_id=row.adapter_id,
                    project_id=row.project_id,
                    correlation_id=row.correlation_id,
                )
                return row

            row = self._store.transition(
                row.task_id,
                STATE_CANCELLING,
                event_type=EVENT_CANCELLATION_REQUESTED,
                actor=ACTOR_USER,
                source=SOURCE_COFFERDAM,
                expected_state=row.state,
                cancellation={"requested_at": now_iso(), "requested_by": ACTOR_USER},
            )
            self._audit(
                "task_cancel",
                "requested",
                task_id=row.task_id,
                adapter_id=row.adapter_id,
                project_id=row.project_id,
                correlation_id=row.correlation_id,
            )

            project = self._projects.get(row.project_id)
            try:
                root = verify_root(project.root)
            except TaskError:
                # The project's folder went away while the task was running.
                # The cancel still has to resolve, so it resolves honestly.
                return self._fail(
                    row,
                    "task_project_root_invalid",
                    "the project folder is no longer usable",
                    None,
                )

            context = self._context(row, root, adapter)
            try:
                outcome = adapter.cancel(context)
            except AdapterRefusal as refusal:
                # The adapter could not stop it. `cancelling` is left in place
                # rather than promoted to `cancelled`: claiming a task stopped
                # because stopping it was requested is the exact false success
                # this whole design refuses to produce.
                self._store.append_event(
                    row.task_id,
                    EVENT_ACTION_REJECTED,
                    actor=ACTOR_SYSTEM,
                    source=SOURCE_COFFERDAM,
                    text="The adapter could not stop this task.",
                    detail=refusal.message,
                )
                self._audit(
                    "task_cancel",
                    "refused",
                    task_id=row.task_id,
                    adapter_id=row.adapter_id,
                    project_id=row.project_id,
                    correlation_id=row.correlation_id,
                )
                return self._store.get(row.task_id)
            except Exception as exc:
                return self._fail(
                    row,
                    "task_adapter_error",
                    "the task adapter stopped unexpectedly",
                    type(exc).__name__,
                )
            return self._apply(row, outcome, cancelling=True)

    # -- restart -------------------------------------------------------------

    def recover_after_restart(self) -> List[TaskRow]:
        """Settle every task the database still believes is unfinished.

        Called once at start-up. Nothing is resumed. Each task becomes
        ``interrupted`` — or ``recovery_required`` if its adapter genuinely
        claims it can reattach — with an event recording that the service
        restarted underneath it. Terminal tasks are never read into this path at
        all, so a completed task cannot be altered by a restart.
        """
        settled: List[TaskRow] = []
        for row in self._store.non_terminal_tasks():
            adapter = self._adapters.find(row.adapter_id)
            recoverable = bool(
                adapter and adapter.capabilities().recover_after_restart
            )
            target = STATE_RECOVERY_REQUIRED if recoverable else STATE_INTERRUPTED
            if not can_transition(row.state, target):  # pragma: no cover - graph invariant
                continue
            try:
                updated = self._store.transition(
                    row.task_id,
                    target,
                    event_type=(
                        "recovery_required" if recoverable else EVENT_TASK_INTERRUPTED
                    ),
                    actor=ACTOR_SYSTEM,
                    source=SOURCE_RESTART_RECOVERY,
                    expected_state=row.state,
                    text=(
                        "Cofferdam restarted while this task was "
                        + row.state
                        + ". It was not resumed."
                    ),
                    failure=None,
                )
            except (IllegalTransition, TaskError):  # pragma: no cover - defensive
                continue
            settled.append(updated)
            self._audit(
                "task_interrupted",
                target,
                task_id=updated.task_id,
                adapter_id=updated.adapter_id,
                project_id=updated.project_id,
                correlation_id=updated.correlation_id,
            )
        return settled

    # -- applying an adapter's report ---------------------------------------

    def _apply(self, row: TaskRow, outcome, *, cancelling: bool = False) -> TaskRow:
        """Record an adapter's events, then its requested state if the graph allows.

        Events first, always. If the requested state is refused, the events that
        led to it are still history — losing them would make an adapter's
        misbehaviour harder to see, not easier.
        """
        for event in outcome.events:
            bounded = event.bounded()
            # Lifecycle event types belong to the core. An adapter that emitted
            # `task_completed` would be writing a completion into the history
            # without passing the transition graph, and the history would then
            # disagree with the snapshot. Demoted rather than dropped: what the
            # adapter said still gets recorded, just not as a lifecycle claim.
            event_type = (
                EVENT_MEANINGFUL_OUTPUT
                if bounded.event_type in CORE_OWNED_EVENT_TYPES
                else bounded.event_type
            )
            self._store.append_event(
                row.task_id,
                event_type,
                actor=outcome.actor,
                source=outcome.source,
                text=bounded.text,
                detail=bounded.detail,
                # Whatever an adapter says about the world is a claim, and it is
                # stamped as one here rather than trusted as passed.
                evidence=tuple(
                    EvidenceReference(
                        evidence_type=reference.evidence_type,
                        source=EVIDENCE_ADAPTER_REPORTED,
                        identifier=reference.identifier,
                        operation=reference.operation,
                        result=reference.result,
                        observed_at=reference.observed_at or now_iso(),
                    )
                    for reference in bounded.evidence
                ),
            )

        # Observations, if any: things Cofferdam ran and saw for itself. They
        # are appended as their own event so the history keeps the two kinds of
        # statement visibly apart — an agent's account of the work, and the
        # result of Cofferdam going and looking.
        observations = tuple(
            reference
            for reference in getattr(outcome, "observations", ())
            if isinstance(reference, EvidenceReference)
        )
        if observations:
            self._store.append_event(
                row.task_id,
                EVENT_PROGRESS,
                actor=ACTOR_SYSTEM,
                source=SOURCE_COFFERDAM,
                text="Cofferdam checked the project itself.",
                evidence=tuple(
                    EvidenceReference(
                        evidence_type=reference.evidence_type,
                        # Checked, not accepted. An adapter that put a claim in
                        # this field gets it demoted to a claim rather than
                        # promoted to an observation — the field is a place to
                        # be believed, not a way to become believable.
                        source=(
                            reference.source
                            if reference.source in VERIFIED_EVIDENCE_SOURCES
                            else EVIDENCE_ADAPTER_REPORTED
                        ),
                        identifier=reference.identifier,
                        operation=reference.operation,
                        result=reference.result,
                        observed_at=reference.observed_at or now_iso(),
                    )
                    for reference in observations[:MAX_EVIDENCE_ITEMS]
                ),
            )

        requested = outcome.requested_state
        if requested is None:
            return self._store.get(row.task_id)

        current = self._store.get(row.task_id)
        if current.state == requested:
            return current
        if not can_transition(current.state, requested):
            # The adapter asked for something the graph does not contain. Its
            # events are kept, the refusal is recorded, and the task stays where
            # it is — an adapter does not get to move a task off the graph.
            self._store.append_event(
                row.task_id,
                EVENT_ACTION_REJECTED,
                actor=ACTOR_SYSTEM,
                source=SOURCE_COFFERDAM,
                text="The adapter asked for a state this task cannot enter.",
                detail=current.state + " → " + str(requested),
            )
            self._audit(
                "task_transition",
                "rejected",
                task_id=row.task_id,
                adapter_id=row.adapter_id,
                project_id=row.project_id,
                correlation_id=row.correlation_id,
            )
            return current

        event_type = {
            STATE_COMPLETED: EVENT_TASK_COMPLETED,
            STATE_FAILED: EVENT_TASK_FAILED,
            STATE_CANCELLED: EVENT_TASK_CANCELLED,
            STATE_WAITING_FOR_USER: EVENT_WAITING_FOR_USER,
            STATE_READY_FOR_FOLLOWUP: EVENT_TURN_COMPLETE,
            STATE_RUNNING: EVENT_TASK_STARTED,
        }.get(requested, EVENT_TASK_STARTED)

        failure = None
        if requested == STATE_FAILED:
            failure = TaskFailure(
                code=outcome.failure_code or "task_failed",
                message=outcome.failure_message or "the task failed",
                detail=None,
            )

        waiting_reason = None
        if requested == STATE_WAITING_FOR_USER:
            waiting_reason = (
                outcome.waiting_reason
                if outcome.waiting_reason in WAITING_REASONS
                else WAITING_UNKNOWN
            )

        updated = self._store.transition(
            row.task_id,
            requested,
            event_type=event_type,
            actor=outcome.actor,
            source=outcome.source,
            expected_state=current.state,
            waiting_reason=waiting_reason,
            final_result=outcome.final_result,
            failure=failure,
            cancellation=(
                {"completed_at": now_iso(), "accepted": True} if cancelling and requested == STATE_CANCELLED else None
            ),
        )
        if requested in TERMINAL_STATES:
            self._audit(
                "task_" + requested,
                "ok",
                task_id=updated.task_id,
                adapter_id=updated.adapter_id,
                project_id=updated.project_id,
                correlation_id=updated.correlation_id,
            )
        return updated

    def _fail(
        self, row: TaskRow, code: str, message: str, detail: Optional[str]
    ) -> TaskRow:
        """End a task as failed, with Cofferdam's words rather than an adapter's.

        ``detail`` is bounded and is an exception *type name* at most — never a
        message, never a traceback, never a path.
        """
        if not can_transition(row.state, STATE_FAILED):  # pragma: no cover - defensive
            return self._store.get(row.task_id)
        updated = self._store.transition(
            row.task_id,
            STATE_FAILED,
            event_type=EVENT_TASK_FAILED,
            actor=ACTOR_SYSTEM,
            source=SOURCE_COFFERDAM,
            expected_state=row.state,
            text=message,
            failure=TaskFailure(code=code, message=message, detail=bounded_line(detail, 200)),
        )
        self._audit(
            "task_failed",
            code,
            task_id=updated.task_id,
            adapter_id=updated.adapter_id,
            project_id=updated.project_id,
            correlation_id=updated.correlation_id,
        )
        return updated

    def _reject(self, row: TaskRow, operation: str, reason: str) -> None:
        """Record that an action was refused, on the task's own history.

        Worth a durable event: "I pressed cancel and nothing happened" is a
        common and confusing experience, and the answer should be visible where
        the task is rather than only in an HTTP response somebody has closed.
        """
        try:
            self._store.append_event(
                row.task_id,
                EVENT_ACTION_REJECTED,
                actor=ACTOR_USER,
                source=SOURCE_COFFERDAM,
                text="Cofferdam refused a " + operation + " request.",
                detail=reason,
            )
        except TaskError:  # pragma: no cover - the task vanished mid-request
            return
        self._audit(
            "task_" + operation,
            "rejected",
            task_id=row.task_id,
            adapter_id=row.adapter_id,
            project_id=row.project_id,
            correlation_id=row.correlation_id,
        )

    # -- validation ----------------------------------------------------------

    def _resolve(self, project_id: object, adapter_id: object):
        project = self._projects.get(project_id)
        adapter = self._adapters.get(adapter_id)
        if not project.permits(adapter.adapter_id):
            raise AdapterNotPermitted()
        return project, adapter

    def _valid_prompt(self, prompt: object) -> str:
        if not isinstance(prompt, str):
            raise PromptInvalid("a prompt is required")
        if not prompt.strip():
            raise PromptInvalid("a prompt cannot be empty")
        if not valid_user_text(prompt, MAX_PROMPT_CHARS):
            # One message for both remaining failures on purpose: saying which
            # control character was found would be repeating the rejected input
            # back into an error response.
            raise PromptInvalid(
                "a prompt must be under "
                + str(MAX_PROMPT_CHARS)
                + " characters and contain no control characters"
            )
        return prompt

    def _valid_followup(self, followup: object) -> str:
        if not isinstance(followup, str) or not followup.strip():
            raise FollowupInvalid("a follow-up cannot be empty")
        if not valid_user_text(followup, MAX_FOLLOWUP_CHARS):
            raise FollowupInvalid(
                "a follow-up must be under "
                + str(MAX_FOLLOWUP_CHARS)
                + " characters and contain no control characters"
            )
        return followup

    def _valid_request_id(self, value: object) -> Optional[str]:
        """A client's own retry key. Opaque, bounded, and never authority.

        It is not a task id and cannot become one: it is used as a *lookup* into
        the idempotency table, scoped to the operation, and the task id it maps
        to was minted by the server. A client that sends a task id here gets
        nothing but a key that happens to look like one.
        """
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise RequestIdInvalid("a request id must be non-empty text")
        if len(value) > MAX_CLIENT_REQUEST_ID_CHARS:
            raise RequestIdInvalid(
                "a request id must be under "
                + str(MAX_CLIENT_REQUEST_ID_CHARS)
                + " characters"
            )
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise RequestIdInvalid("a request id must not contain control characters")
        return value

    def _context(
        self,
        row: TaskRow,
        root: Path,
        adapter: TaskAdapter,
        followup: Optional[str] = None,
    ) -> TaskContext:
        return TaskContext(
            task_id=row.task_id,
            correlation_id=row.correlation_id,
            project_id=row.project_id,
            project_root=root,
            adapter_id=adapter.adapter_id,
            prompt=row.prompt,
            state=row.state,
            lifecycle_revision=row.lifecycle_revision,
            followup=followup,
        )

    # -- health --------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """What the status surface may say about tasks. No content, ever."""
        return {
            "store": self._store.storage_health(),
            "adapters": self._adapters.ids(),
            "projects_configured": len(self._projects.projects),
            "project_problems": len(self._projects.problems),
        }


__all__ = ["AuditHook", "TaskService"]
