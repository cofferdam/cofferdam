"""Lifecycle supervision for native Claude Remote Control hosts.

This is Lane A, and it is deliberately small. It answers three questions about a
registered project — is its host up, bring it up, take it down — and it has no
opinion about anything that happens inside the session it supervises.

What this is not
----------------

**Not a second Task Core.** There is no queue, no persistence, no state machine,
no event log, no follow-up channel and no cancellation semantics here. Task Core
owns delegated work (Lane B); this package owns a process's lifecycle. The two
share the project registry and nothing else, and this module imports from
:mod:`..tasks.projects` only — never from ``tasks.service``, ``tasks.store`` or
``tasks.adapters``.

**Not a session reader.** Cofferdam supervises the host and never reads what the
host is doing. There is no transcript path, no hook, no log parse and no field
to put conversation content in. That boundary is permanent under D-2026-08-08-3
unless Anthropic ships a supported session-content API.

**Not a source of truth about Claude.** ``systemctl`` knows about a process. The
states this supervisor can report are therefore process states, and every one of
them is derived in :func:`..model.map_active_state` from an ``ActiveState`` a
real manager produced. ``connected``, ``authenticated`` and ``auth_required``
are absent because nothing here can observe them; the PR that adds evidence for
them is the PR that may add the states.

``awaiting_consent`` is the one field that *did* earn its evidence. The M2H PR2
PTY spike watched the real CLI stop on ``Enable Remote Control? (y/n)`` and wait
for an answer that ``stdin=/dev/null`` can never give it, so a unit can be
``active`` and healthy while nothing is reachable from a phone. That gap between
"the process runs" and "the session exists" is reported rather than papered
over — see :func:`..wrapper.detect_consent_required`.

The capability gate, and the one place it does not apply
--------------------------------------------------------

Two different fields decide this, and they are easy to confuse:

``enabled``
    The project registry's own switch, checked by :meth:`ProjectRegistry.get`.
    A project that is off is off for everything — start, stop and status all
    refuse it.
``remote_control_enabled``
    The Lane A capability added in M2H. It gates **start, and only start**.

So :meth:`RemoteControlSupervisor.start` requires the capability;
:meth:`~RemoteControlSupervisor.stop` and :meth:`~RemoteControlSupervisor.status`
deliberately do not, while still requiring the project to be registered and
``enabled``. The asymmetry is on purpose: revoking the capability on a project
whose host is *already running* must not strand a live process with no
supervised way to shut it down. The gate exists to control what may be
**created**, and refusing to stop something is not a smaller permission than
refusing to start it — it is a different and worse one. ``status`` stays open
for the same reason: nobody can decide to stop what they may not look at.

Neither ``stop`` nor ``status`` verifies the project *root*. That check belongs
to :func:`..host.resolve`, immediately before the exec, because a root that has
been deleted or moved must not make a running host unstoppable — the same
stranding argument, applied to the filesystem instead of the flag.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from ..tasks.errors import ProjectDisabled, ProjectUnknown
from ..tasks.projects import ProjectRegistry, TaskProject
from .errors import (
    LinkUnavailable,
    RemoteControlError,
    RemoteControlNotEnabled,
    SessionProjectDisabled,
    SessionProjectUnknown,
)
from . import links
from .model import (
    LIVE_STATES,
    STATE_STOPPED,
    NativeSessionStatus,
)
from .state import LinkStore
from .systemd import SystemdUserBackend

#: Supplies the current project registry. A callable rather than a value so the
#: supervisor sees host configuration edits without a daemon restart, the same
#: way the rest of the workstation treats host-owned configuration.
RegistryProvider = Callable[[], ProjectRegistry]

#: Returns an ISO-8601 UTC timestamp. Cofferdam's own clock, distinct from the
#: verbatim systemd timestamp in :attr:`~.model.NativeSessionStatus.started_at`.
Clock = Callable[[], str]


#: How many times a fresh start re-reads the runtime state before giving up on
#: learning its generation, and how long it waits between attempts.
#:
#: Bounded on purpose, and small: the host writes its generation as one of the
#: first things it does, so this is covering a scheduling gap, not waiting for
#: Claude to be useful. Four attempts a quarter-second apart is under a second
#: of added latency on a start that is already a process launch.
START_SETTLE_ATTEMPTS = 4
START_SETTLE_SECONDS = 0.25


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class RemoteControlSupervisor:
    """Start, stop and inspect one native Remote Control host per project."""

    def __init__(
        self,
        registry_provider: RegistryProvider,
        *,
        backend: Optional[SystemdUserBackend] = None,
        store: Optional[LinkStore] = None,
        clock: Optional[Clock] = None,
        sleep: Optional[Callable[[float], None]] = None,
        config=None,
    ) -> None:
        self._registry_provider = registry_provider
        self._backend = backend if backend is not None else SystemdUserBackend()
        self._store = store if store is not None else LinkStore(config)
        self._clock = clock if clock is not None else utc_now_iso
        self._sleep = sleep if sleep is not None else time.sleep

    # -- authority -----------------------------------------------------------

    def _project(self, project_id: object) -> TaskProject:
        """Resolve a registered, enabled project, or refuse.

        The id is the only thing a caller ever supplies. There is no parameter
        on any method in this class that takes a path, an executable, a flag, an
        environment mapping or a command — the project registry is the only
        thing that can name a directory, and it is host-owned.
        """
        registry = self._registry_provider()
        try:
            return registry.get(project_id)
        except ProjectDisabled as exc:
            raise SessionProjectDisabled() from exc
        except ProjectUnknown as exc:
            raise SessionProjectUnknown() from exc

    def _project_for_start(self, project_id: object) -> TaskProject:
        project = self._project(project_id)
        if not project.remote_control_enabled:
            raise RemoteControlNotEnabled()
        return project

    # -- operations ----------------------------------------------------------

    def status(self, project_id: str) -> NativeSessionStatus:
        """What this workstation can truthfully say about that project's host.

        Two independent sources, kept apart. systemd answers "is the process
        up"; the runtime state file answers "did that process report a session
        link". Neither is allowed to imply the other: a running unit with no
        link is ``running`` with ``url_available=False``, and a leftover link
        without a running unit is not reported at all.
        """
        project = self._project(project_id)
        status = self._backend.status(project.project_id)
        return self._stamp(self._with_link_evidence(status))

    def link(self, project_id: str) -> Dict[str, Any]:
        """The current session link, or refuse.

        The only operation in Cofferdam that returns a Remote Control URL. It is
        deliberately separate from :meth:`status` so the capability material has
        its own route, its own audit line, and no presence in a payload that
        gets cached and rendered.

        Refuses whenever the link is not *currently* live: never started, host
        stopped, link not yet reported, or the stored link belongs to a previous
        generation. All four are one refusal, because the answer to "give me the
        link" is the same in each.
        """
        project = self._project(project_id)

        # The capture gate, restated at the boundary that hands the capability
        # out. `find_link` already refuses to recognise anything while the
        # format is unconfirmed, so nothing can have been stored — this is the
        # second lock on the same door, and it is here because "nothing could
        # have been stored" is an argument about other code, while this is a
        # fact about this function.
        # Read through the module rather than imported by value: a `from ... import`
        # would bind the flag at import time, so the one place that decides
        # whether links exist would silently stop being the one place.
        if not links.LINK_FORMAT_CONFIRMED:
            raise LinkUnavailable(
                "the session link format has not been confirmed on this build, "
                "so no link is captured"
            )

        status = self._backend.status(project.project_id)

        # The unit must be up. A link whose process has exited is a dead
        # capability, and handing one out would be worse than saying no.
        if status.state not in LIVE_STATES:
            raise LinkUnavailable()

        document = self._store.read(project.project_id)
        if not document or not document.get("link"):
            raise LinkUnavailable()

        generation = document.get("generation")
        if not generation:
            raise LinkUnavailable()

        return {
            "project_id": project.project_id,
            "generation": generation,
            "url": document["link"],
            "discovered_at": document.get("discovered_at"),
            "retrieved_at": self._clock(),
        }

    def start(self, project_id: str) -> NativeSessionStatus:
        """Bring the host up, or report that it already is.

        Idempotent by asking first. A host that is already ``running`` or
        ``starting`` is returned as-is and no start is issued, so a phone that
        double-taps does not get two hosts in one project directory — which
        would be two Remote Control servers competing for the same working
        directory, not a harmless duplicate.
        """
        project = self._project_for_start(project_id)
        current = self._backend.status(project.project_id)
        if current.state in LIVE_STATES:
            return self._stamp(self._with_link_evidence(current))
        self._backend.start(project.project_id)
        return self._stamp(self._settle(project.project_id))

    def _settle(self, project_id: str) -> NativeSessionStatus:
        """Status once the new host has had a moment to identify itself.

        ``systemctl start`` returns when the fork succeeds, not when the program
        has done anything. The generation is minted by the *host* process and
        written to the runtime state file a moment later, so reading status
        immediately after a start returns ``generation: null`` — which the M2H
        PR2 live validation caught by asking whether a second launch differed
        from the first and getting ``None != None``.

        A null generation is not merely cosmetic. It is the value clients use to
        tell one launch from another, so a start response that omits it lets a
        caller mistake a fresh host for the one it already had, and makes an
        idempotency check pass by comparing two blanks.

        So: a short bounded wait for the host to say who it is, and no more than
        that. If it never does, the status is returned as it stands with a null
        generation rather than an invented one — being slow to identify itself
        is not a reason to refuse a start that systemd accepted.

        systemd is asked once and only once. What is racing here is the *state
        file*, not the unit, and re-running ``systemctl show`` on a loop to
        watch a value it does not hold would just be several subprocesses to
        learn the same thing.
        """
        status = self._backend.status(project_id)
        for attempt in range(START_SETTLE_ATTEMPTS):
            settled = self._with_link_evidence(status)
            if settled.generation is not None or not settled.is_live():
                return settled
            if attempt + 1 < START_SETTLE_ATTEMPTS:
                self._sleep(START_SETTLE_SECONDS)
        return self._with_link_evidence(status)

    def stop(self, project_id: str) -> NativeSessionStatus:
        """Take the host down, or report that it is already down.

        A stop against something already stopped is a truthful ``stopped``, not
        a failure: the caller asked for a state, the state holds, and inventing
        an error would teach a client to retry something that already succeeded.
        """
        project = self._project(project_id)
        current = self._backend.status(project.project_id)
        if current.state == STATE_STOPPED:
            self._forget(project.project_id)
            return self._stamp(current)
        self._backend.stop(project.project_id)
        # The host's own `finally` clears this too. Doing it here as well means
        # a child killed hard enough to skip its cleanup still cannot leave a
        # retrievable capability URL behind.
        self._forget(project.project_id)
        return self._stamp(self._backend.status(project.project_id))

    # -- helpers -------------------------------------------------------------

    def _forget(self, project_id: str) -> None:
        """Drop any stored link. Never fails a stop.

        A stop that reported failure because a state file could not be deleted
        would be a stop somebody retries forever while the process is already
        gone. The link is unusable either way: :meth:`link` refuses whenever the
        unit is not live.
        """
        try:
            self._store.clear(project_id)
        except RemoteControlError:
            return

    def _with_link_evidence(self, status: NativeSessionStatus) -> NativeSessionStatus:
        """Fold the runtime state file into a systemd-derived status.

        Conservative in one direction only. Link and auth evidence are attached
        **only** while the unit is live: a state file that outlived its process
        describes a host that no longer exists, and reporting
        ``url_available=True`` for it would send somebody to a dead session.

        A state file that cannot be read is not an error here. Status must keep
        answering when the link store is unavailable — the systemd half of the
        answer is still true and still useful.
        """
        if not status.is_live():
            return status
        try:
            document = self._store.read(status.project_id)
        except RemoteControlError:
            return replace(status, error=status.error or "the runtime state could not be read")
        if not document:
            return status
        return replace(
            status,
            generation=document.get("generation"),
            url_available=bool(document.get("link")),
            auth_required=bool(document.get("auth_required")),
            awaiting_consent=bool(document.get("awaiting_consent")),
        )

    def _stamp(self, status: NativeSessionStatus) -> NativeSessionStatus:
        """Record when Cofferdam last got an answer it could read.

        Only ever set here, and only on a status object that came back from the
        backend — so ``last_seen_at`` means "this is when the manager answered",
        never "this is when we assumed".
        """
        return replace(status, last_seen_at=self._clock())


__all__ = [
    "Clock",
    "RegistryProvider",
    "RemoteControlSupervisor",
    "START_SETTLE_ATTEMPTS",
    "START_SETTLE_SECONDS",
    "utc_now_iso",
]
