"""Test doubles for the native-session package.

The important one is :class:`FakeRunner`. Every ``systemctl`` invocation the
supervisor can make goes through an injected runner, so these tests prove the
exact argv, the timeout and the parsing without a systemd on the other end —
and, more to the point, **without the possibility** of a test starting a real
Remote Control host or touching the live user manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cofferdam.workstation.tasks.projects import ProjectRegistry, TaskProject


class FakeCompleted:
    """The subset of ``CompletedProcess`` the backend reads."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def show_output(
    load_state: str = "loaded",
    active_state: str = "inactive",
    sub_state: str = "dead",
    active_enter: str = "",
) -> bytes:
    """A realistic ``systemctl show`` reply, in the order the real one emits."""
    return (
        "LoadState=" + load_state + "\n"
        "ActiveState=" + active_state + "\n"
        "SubState=" + sub_state + "\n"
        "ActiveEnterTimestamp=" + active_enter + "\n"
    ).encode("utf-8")


class FakeRunner:
    """Records every call and replies from a scripted queue.

    ``replies`` is consumed in order; when it runs out the default is returned.
    That makes a start-then-status sequence expressible as two entries, which is
    what the idempotency tests need.
    """

    def __init__(
        self,
        replies: Optional[Sequence[FakeCompleted]] = None,
        default: Optional[FakeCompleted] = None,
    ) -> None:
        self.calls: List[Tuple[List[str], Dict[str, Any]]] = []
        self._replies = list(replies or [])
        self._default = default if default is not None else FakeCompleted(0, show_output())

    def __call__(self, argv, **kwargs) -> FakeCompleted:
        self.calls.append((list(argv), dict(kwargs)))
        if self._replies:
            return self._replies.pop(0)
        return self._default

    # -- convenience ---------------------------------------------------------

    @property
    def argvs(self) -> List[List[str]]:
        return [argv for argv, _ in self.calls]

    def argvs_containing(self, verb: str) -> List[List[str]]:
        return [argv for argv in self.argvs if verb in argv]

    @property
    def timeouts(self) -> List[Any]:
        return [kwargs.get("timeout") for _, kwargs in self.calls]


class RaisingRunner:
    """A runner that fails the way ``run_fixed`` does when systemctl is absent."""

    def __init__(self, exception: BaseException) -> None:
        self._exception = exception
        self.calls: List[List[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        raise self._exception


def make_project(
    project_id: str = "demo",
    *,
    root: str = "/srv/demo",
    enabled: bool = True,
    remote_control_enabled: bool = True,
    adapters: Tuple[str, ...] = (),
) -> TaskProject:
    return TaskProject(
        project_id=project_id,
        display_name=project_id,
        root=Path(root),
        enabled=enabled,
        adapters=adapters,
        remote_control_enabled=remote_control_enabled,
    )


def make_registry(*projects: TaskProject) -> ProjectRegistry:
    return ProjectRegistry(projects=tuple(projects), source_present=True)


def provider(*projects: TaskProject):
    """A registry provider closing over a fixed set of projects."""
    registry = make_registry(*projects)
    return lambda: registry


def fixed_clock(value: str = "2026-08-08T00:00:00+00:00"):
    return lambda: value


__all__ = [
    "FakeCompleted",
    "FakeRunner",
    "RaisingRunner",
    "fixed_clock",
    "make_project",
    "make_registry",
    "provider",
    "show_output",
]
