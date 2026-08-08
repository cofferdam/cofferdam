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
real manager produced. ``connected``, ``authenticated``, ``waiting_for_user``
and ``auth_required`` are absent because nothing here can observe them; the PR
that adds evidence for them is the PR that may add the states.

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
from dataclasses import replace
from typing import Callable, Optional

from ..tasks.errors import ProjectDisabled, ProjectUnknown
from ..tasks.projects import ProjectRegistry, TaskProject
from .errors import (
    RemoteControlNotEnabled,
    SessionProjectDisabled,
    SessionProjectUnknown,
)
from .model import (
    LIVE_STATES,
    STATE_STOPPED,
    NativeSessionStatus,
)
from .systemd import SystemdUserBackend

#: Supplies the current project registry. A callable rather than a value so the
#: supervisor sees host configuration edits without a daemon restart, the same
#: way the rest of the workstation treats host-owned configuration.
RegistryProvider = Callable[[], ProjectRegistry]

#: Returns an ISO-8601 UTC timestamp. Cofferdam's own clock, distinct from the
#: verbatim systemd timestamp in :attr:`~.model.NativeSessionStatus.started_at`.
Clock = Callable[[], str]


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class RemoteControlSupervisor:
    """Start, stop and inspect one native Remote Control host per project."""

    def __init__(
        self,
        registry_provider: RegistryProvider,
        *,
        backend: Optional[SystemdUserBackend] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._registry_provider = registry_provider
        self._backend = backend if backend is not None else SystemdUserBackend()
        self._clock = clock if clock is not None else utc_now_iso

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
        """What this workstation can truthfully say about that project's host."""
        project = self._project(project_id)
        return self._stamp(self._backend.status(project.project_id))

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
            return self._stamp(current)
        self._backend.start(project.project_id)
        return self._stamp(self._backend.status(project.project_id))

    def stop(self, project_id: str) -> NativeSessionStatus:
        """Take the host down, or report that it is already down.

        A stop against something already stopped is a truthful ``stopped``, not
        a failure: the caller asked for a state, the state holds, and inventing
        an error would teach a client to retry something that already succeeded.
        """
        project = self._project(project_id)
        current = self._backend.status(project.project_id)
        if current.state == STATE_STOPPED:
            return self._stamp(current)
        self._backend.stop(project.project_id)
        return self._stamp(self._backend.status(project.project_id))

    # -- helpers -------------------------------------------------------------

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
    "utc_now_iso",
]
