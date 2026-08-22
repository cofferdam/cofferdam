"""Cofferdam-owned Claude sessions: the machinery, shared by every component.

Two modules, and neither is bound to a component:

:mod:`.session`
    Where a Cofferdam-owned Claude credential lives, what may be said about it
    without opening it, how it is locked, and the exact environment a subprocess
    using it receives. Parameterised by a :class:`~.session.ClaudeSessionNamespace`.
:mod:`.cli`
    The one-time human sign-in, likewise parameterised.

Written for the worker in M2L PR1g and extracted here in M2M PR4, when the
development planner needed a session of its own and the only alternatives were
sharing the worker's credential or copying it — the two things PR1g exists to
refuse.

The bindings live with their components:
:mod:`cofferdam.workstation.worker.session` and
:mod:`cofferdam.workstation.planner.session`. Each supplies a directory name, a
label and an exception type, and inherits everything else. Nothing here knows
what a worker or a planner is.

**It is its own package rather than two modules beside the daemon** so the
subprocess exemption in ``tests/test_workstation_no_shell.py`` can go on naming
``(filename, parent)`` pairs. A file directly under ``workstation/`` would have
forced that guard to spell ``path.parent.name == "workstation"``, which is
exactly the package-wide exemption
``tests/test_git_baseline_guard_exactness.py`` refuses to allow.
"""

from __future__ import annotations

__all__ = ["cli", "session"]
