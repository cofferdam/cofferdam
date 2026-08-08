"""M2H PR1 — the supervised Remote Control session foundation.

Everything here is standard-library only, so it runs on the stdlib-only CI path.

No test in this file may start a real Remote Control host, run ``systemctl``,
reach the network, require a Claude login, or read the live project registry.
Every subprocess boundary is injected: the systemd backend takes a runner, and
the host entry point takes ``chdir`` and ``exec_fn``. :class:`NoLiveEffectTests`
at the bottom asserts that property against the source rather than trusting it.
"""

from __future__ import annotations

import ast
import contextlib
import re
import io
import unittest
from pathlib import Path
from typing import List

from cofferdam.workstation.sessions import claude, host, model, systemd, units
from cofferdam.workstation.sessions.errors import (
    BackendRefused,
    BackendUnavailable,
    ExecutableMissing,
    RemoteControlNotEnabled,
    SessionProjectDisabled,
    SessionProjectUnknown,
    SessionRootInvalid,
)
from cofferdam.workstation.sessions.model import (
    STATE_FAILED,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_STOPPING,
    STATE_UNKNOWN,
    NativeSessionStatus,
)
from cofferdam.workstation.sessions.supervisor import RemoteControlSupervisor
from cofferdam.workstation.sessions.systemd import SystemdUserBackend
from cofferdam.workstation.tasks.projects import TaskProject, _read_project

from ._sessions_doubles import (
    FakeCompleted,
    FakeRunner,
    MemoryLinkStore,
    RaisingRunner,
    fixed_clock,
    make_project,
    provider,
    show_output,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "deploy" / "cofferdam-rc@.service"


def _string_literals(path: Path) -> List[str]:
    """Every string constant in a module except its docstrings.

    Structural tests below assert what the code can *act on*. Prose explaining
    why something is not done lives in docstrings, and matching it would make a
    good explanation into a failing test.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _supervisor(runner, *projects, clock=None, store=None):
    return RemoteControlSupervisor(
        provider(*projects),
        backend=SystemdUserBackend(runner),
        store=store if store is not None else MemoryLinkStore(),
        clock=clock or fixed_clock(),
    )


# ---------------------------------------------------------------------------
# Project and unit identity
# ---------------------------------------------------------------------------


class UnitIdentityTests(unittest.TestCase):
    def test_valid_project_produces_the_expected_unit(self) -> None:
        self.assertEqual(units.unit_name("demo"), "cofferdam-rc@demo.service")

    def test_unit_name_is_deterministic(self) -> None:
        self.assertEqual(units.unit_name("my-project"), units.unit_name("my-project"))

    def test_the_project_id_appears_verbatim(self) -> None:
        """No escaping step, so the unit name is greppable from the config file."""
        self.assertIn("my_project-2", units.unit_name("my_project-2"))

    def test_unsafe_identifiers_are_refused(self) -> None:
        for candidate in (
            "",
            "../../tmp",
            "/home/user/project",
            "demo project",
            "demo\nproject",
            "demo;rm -rf /",
            "demo$(id)",
            "demo`id`",
            "demo|id",
            "demo&id",
            "demo.service",
            "demo@other",
            "demo%i",
            "DEMO",
            "demo/../etc",
            "a" * 65,
            "..",
            ".",
            "~",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(SessionProjectUnknown):
                    units.unit_name(candidate)

    def test_non_string_identifiers_are_refused(self) -> None:
        for candidate in (None, 1, True, [], {}, object()):
            with self.subTest(candidate=candidate):
                with self.assertRaises(SessionProjectUnknown):
                    units.unit_name(candidate)  # type: ignore[arg-type]

    def test_no_path_separator_can_reach_the_unit_instance(self) -> None:
        for candidate in ("a/b", "a\\b", "..", "/", "./x"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(SessionProjectUnknown):
                    units.unit_name(candidate)

    def test_unit_names_round_trip(self) -> None:
        self.assertEqual(units.project_id_from_unit("cofferdam-rc@demo.service"), "demo")

    def test_foreign_units_do_not_round_trip(self) -> None:
        for unit in (
            "cofferdam-workstation.service",
            "cofferdam-rc@.service",
            "cofferdam-rc@BAD.service",
            "other@demo.service",
            "cofferdam-rc@demo.socket",
            "",
            None,
        ):
            with self.subTest(unit=unit):
                self.assertIsNone(units.project_id_from_unit(unit))  # type: ignore[arg-type]


class ProjectResolutionTests(unittest.TestCase):
    def test_registered_enabled_project_resolves(self) -> None:
        runner = FakeRunner()
        supervisor = _supervisor(runner, make_project("demo"))
        status = supervisor.status("demo")
        self.assertEqual(status.project_id, "demo")
        self.assertEqual(status.unit, "cofferdam-rc@demo.service")

    def test_unknown_project_is_refused(self) -> None:
        supervisor = _supervisor(FakeRunner(), make_project("demo"))
        with self.assertRaises(SessionProjectUnknown):
            supervisor.status("nope")

    def test_unknown_project_never_reaches_systemctl(self) -> None:
        runner = FakeRunner()
        supervisor = _supervisor(runner, make_project("demo"))
        for method in ("status", "start", "stop"):
            with self.subTest(method=method):
                with self.assertRaises(SessionProjectUnknown):
                    getattr(supervisor, method)("nope")
        self.assertEqual(runner.calls, [])

    def test_registry_disabled_project_is_refused_everywhere(self) -> None:
        runner = FakeRunner()
        supervisor = _supervisor(runner, make_project("demo", enabled=False))
        for method in ("status", "start", "stop"):
            with self.subTest(method=method):
                with self.assertRaises(SessionProjectDisabled):
                    getattr(supervisor, method)("demo")
        self.assertEqual(runner.calls, [])

    def test_start_requires_the_capability(self) -> None:
        runner = FakeRunner()
        supervisor = _supervisor(runner, make_project("demo", remote_control_enabled=False))
        with self.assertRaises(RemoteControlNotEnabled):
            supervisor.start("demo")
        self.assertEqual(runner.calls, [], "a refused start must not reach systemctl")

    def test_stop_and_status_survive_a_revoked_capability(self) -> None:
        """Deliberate asymmetry: revoking the flag must not strand a live host.

        The gate controls what may be *created*. Refusing to stop a running
        Remote Control host because somebody just turned the capability off
        would leave a process nobody has a supervised way to shut down.
        """
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="active", sub_state="running")))
        supervisor = _supervisor(runner, make_project("demo", remote_control_enabled=False))
        self.assertEqual(supervisor.status("demo").state, STATE_RUNNING)
        supervisor.stop("demo")
        self.assertTrue(runner.argvs_containing("stop"))


class RevokedCapabilityContractTests(unittest.TestCase):
    """The full authority matrix, stated once so it cannot drift.

    Two different fields are involved and confusing them is easy, so they are
    spelled out here:

    ``enabled``
        The registry's own switch. A project that is off is off for everything;
        all three operations refuse it.
    ``remote_control_enabled``
        The Lane A capability. It gates **start only**.

    The asymmetry is deliberate. The capability controls what may be *created*.
    If revoking it also blocked ``stop``, then turning the flag off on a project
    whose host is currently running would strand a live interactive agent with
    no supervised way to shut it down — a strictly worse outcome than the one
    the flag exists to prevent. ``status`` stays open for the same reason: you
    cannot decide to stop something you are not allowed to look at.
    """

    def _supervisor_for(self, **kwargs):
        runner = FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )
        return runner, _supervisor(runner, make_project("demo", **kwargs))

    # -- unknown project: everything refused ---------------------------------

    def test_unknown_project_refuses_start_status_and_stop(self) -> None:
        runner, supervisor = self._supervisor_for(remote_control_enabled=True)
        for operation in ("start", "status", "stop"):
            with self.subTest(operation=operation):
                with self.assertRaises(SessionProjectUnknown):
                    getattr(supervisor, operation)("not-registered")
        self.assertEqual(runner.calls, [], "a refusal must not reach systemctl")

    # -- capability revoked: start refused, status and stop allowed ----------

    def test_revoked_capability_refuses_start_only(self) -> None:
        _, supervisor = self._supervisor_for(remote_control_enabled=False)
        with self.assertRaises(RemoteControlNotEnabled):
            supervisor.start("demo")

    def test_revoked_capability_still_allows_status(self) -> None:
        """A failed or running unit stays observable after revocation."""
        for active_state, expected in (
            ("active", STATE_RUNNING),
            ("failed", STATE_FAILED),
            ("inactive", STATE_STOPPED),
        ):
            with self.subTest(active_state=active_state):
                runner = FakeRunner(
                    default=FakeCompleted(0, show_output(active_state=active_state))
                )
                supervisor = _supervisor(
                    runner, make_project("demo", remote_control_enabled=False)
                )
                self.assertEqual(supervisor.status("demo").state, expected)

    def test_revoked_capability_still_allows_stop(self) -> None:
        """Revocation must not strand a running host."""
        runner, supervisor = self._supervisor_for(remote_control_enabled=False)
        status = supervisor.stop("demo")
        self.assertEqual(
            runner.argvs_containing("stop"),
            [["systemctl", "--user", "stop", "cofferdam-rc@demo.service"]],
        )
        self.assertIsNotNone(status)

    # -- capability granted: everything allowed ------------------------------

    def test_enabled_capability_allows_start_status_and_stop(self) -> None:
        for operation in ("start", "status", "stop"):
            with self.subTest(operation=operation):
                _, supervisor = self._supervisor_for(remote_control_enabled=True)
                self.assertIsNotNone(getattr(supervisor, operation)("demo"))

    # -- the registry switch is not the capability ---------------------------

    def test_a_registry_disabled_project_refuses_everything(self) -> None:
        """`enabled=false` is broader than the capability and blocks all three."""
        runner = FakeRunner()
        supervisor = _supervisor(
            runner, make_project("demo", enabled=False, remote_control_enabled=True)
        )
        for operation in ("start", "status", "stop"):
            with self.subTest(operation=operation):
                with self.assertRaises(SessionProjectDisabled):
                    getattr(supervisor, operation)("demo")
        self.assertEqual(runner.calls, [])

    def test_status_and_stop_resolve_through_the_registry(self) -> None:
        """Not a bypass: the project is still looked up, just not gated on the flag.

        The unit name every operation acts on is derived from the *registered*
        project id, so a caller cannot reach a unit the registry does not name.
        """
        runner, supervisor = self._supervisor_for(remote_control_enabled=False)
        supervisor.status("demo")
        supervisor.stop("demo")
        for argv in runner.argvs:
            with self.subTest(argv=argv):
                self.assertIn("cofferdam-rc@demo.service", argv)

    def test_stop_does_not_require_the_project_root_to_still_exist(self) -> None:
        """Stopping is de-escalation and must not depend on the filesystem.

        The supervisor resolves the project but never calls ``verify_root``: a
        directory deleted or moved while a host is running would otherwise make
        that host unstoppable, which is the same stranding failure the
        capability asymmetry exists to prevent. Root verification belongs at the
        point of *launch* — ``host.resolve`` — where it is checked immediately
        before the exec.
        """
        runner = FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )
        supervisor = _supervisor(
            runner,
            make_project("demo", root="/definitely/not/a/real/path", remote_control_enabled=False),
        )
        supervisor.stop("demo")
        self.assertTrue(runner.argvs_containing("stop"))


class RegistryCapabilityTests(unittest.TestCase):
    """The flag is parsed by the existing project registry, not a new one."""

    def _entry(self, **extra):
        entry = {"project_id": "demo", "root": "/srv/demo"}
        entry.update(extra)
        return entry

    def test_absent_field_defaults_to_disabled(self) -> None:
        project, problem = _read_project(self._entry(), ())
        self.assertIsNone(problem)
        self.assertIsNotNone(project)
        self.assertFalse(project.remote_control_enabled)

    def test_records_without_the_field_stay_valid(self) -> None:
        """Backward compatibility: an M2F-era entry still loads."""
        project, problem = _read_project(
            self._entry(display_name="Demo", enabled=True, adapters=[], notes="x"), ()
        )
        self.assertIsNone(problem)
        self.assertIsNotNone(project)

    def test_explicit_true_enables(self) -> None:
        project, problem = _read_project(self._entry(remote_control_enabled=True), ())
        self.assertIsNone(problem)
        self.assertTrue(project.remote_control_enabled)

    def test_explicit_false_disables(self) -> None:
        project, _ = _read_project(self._entry(remote_control_enabled=False), ())
        self.assertFalse(project.remote_control_enabled)

    def test_malformed_values_are_rejected_not_coerced(self) -> None:
        for value in ("true", "yes", 1, 0, "", None, [], {}, "True"):
            with self.subTest(value=value):
                project, problem = _read_project(
                    self._entry(remote_control_enabled=value), ()
                )
                self.assertIsNone(project, "a malformed capability must not load")
                self.assertIsNotNone(problem)
                self.assertIn("remote_control_enabled", problem["problem"])

    def test_the_dataclass_default_is_off(self) -> None:
        project = TaskProject(project_id="x", display_name="x", root=Path("/srv/x"))
        self.assertFalse(project.remote_control_enabled)

    def test_the_capability_carries_no_execution(self) -> None:
        """A boolean and nothing else — no flag, path, model or command."""
        project, _ = _read_project(self._entry(remote_control_enabled=True), ())
        self.assertIsInstance(project.remote_control_enabled, bool)

    def test_the_flag_is_not_published_to_clients(self) -> None:
        """No route change in this PR: the client payload is untouched."""
        project = make_project("demo", remote_control_enabled=True)
        self.assertNotIn("remote_control_enabled", project.to_dict())

    def test_forbidden_execution_fields_are_still_refused(self) -> None:
        for field in ("argv", "command", "env", "executable", "shell", "token"):
            with self.subTest(field=field):
                project, problem = _read_project(self._entry(**{field: "x"}), ())
                self.assertIsNone(project)
                self.assertIsNotNone(problem)


# ---------------------------------------------------------------------------
# systemd invocation
# ---------------------------------------------------------------------------


class SystemdInvocationTests(unittest.TestCase):
    def test_every_command_is_user_scoped(self) -> None:
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="active")))
        supervisor = _supervisor(runner, make_project("demo"))
        supervisor.status("demo")
        supervisor.stop("demo")
        self.assertTrue(runner.argvs)
        for argv in runner.argvs:
            with self.subTest(argv=argv):
                self.assertEqual(argv[0], "systemctl")
                self.assertIn("--user", argv)
                self.assertNotIn("--system", argv)
                self.assertNotIn("--host", argv)
                self.assertNotIn("--machine", argv)

    def test_argv_is_a_list_of_separate_words(self) -> None:
        """Never one string: a joined command line is a shell waiting to happen."""
        for argv in (
            systemd.show_argv("cofferdam-rc@demo.service"),
            systemd.start_argv("cofferdam-rc@demo.service"),
            systemd.stop_argv("cofferdam-rc@demo.service"),
        ):
            with self.subTest(argv=argv):
                self.assertIsInstance(argv, list)
                for word in argv:
                    self.assertIsInstance(word, str)
                    self.assertNotIn(" ", word)

    def test_start_targets_the_expected_unit(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="inactive")),
                FakeCompleted(0),
                FakeCompleted(0, show_output(active_state="active", sub_state="running")),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        supervisor.start("demo")
        starts = runner.argvs_containing("start")
        self.assertEqual(
            starts, [["systemctl", "--user", "start", "cofferdam-rc@demo.service"]]
        )

    def test_stop_targets_the_expected_unit(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="active", sub_state="running")),
                FakeCompleted(0),
                FakeCompleted(0, show_output(active_state="inactive")),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        supervisor.stop("demo")
        self.assertEqual(
            runner.argvs_containing("stop"),
            [["systemctl", "--user", "stop", "cofferdam-rc@demo.service"]],
        )

    def test_status_requests_exactly_the_properties_it_needs(self) -> None:
        runner = FakeRunner()
        _supervisor(runner, make_project("demo")).status("demo")
        argv = runner.argvs[0]
        self.assertEqual(argv[:4], ["systemctl", "--user", "show", "cofferdam-rc@demo.service"])
        self.assertEqual(
            argv[4:],
            [
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=ActiveEnterTimestamp",
            ],
        )

    def test_status_reads_no_journal(self) -> None:
        runner = FakeRunner()
        _supervisor(runner, make_project("demo")).status("demo")
        for argv in runner.argvs:
            self.assertNotIn("journalctl", argv[0])
            for word in argv:
                self.assertNotIn("--lines", word)
                self.assertNotIn("cat", word.split("=")[-1:] or [""])

    def test_every_call_is_bounded_by_a_timeout(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="inactive")),
                FakeCompleted(0),
                FakeCompleted(0, show_output(active_state="active")),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        supervisor.start("demo")
        self.assertTrue(runner.timeouts)
        for timeout in runner.timeouts:
            with self.subTest(timeout=timeout):
                self.assertIsInstance(timeout, int)
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, 60)

    def test_query_and_control_timeouts_are_distinct_and_bounded(self) -> None:
        self.assertLess(systemd.QUERY_TIMEOUT_SECONDS, systemd.CONTROL_TIMEOUT_SECONDS)
        self.assertLessEqual(systemd.CONTROL_TIMEOUT_SECONDS, 60)

    def test_a_failed_control_command_becomes_a_structured_error(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="inactive")),
                FakeCompleted(1, b"", b"Failed to start cofferdam-rc@demo.service: Unit not found."),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        with self.assertRaises(BackendRefused) as caught:
            supervisor.start("demo")
        self.assertEqual(caught.exception.code, "remote_control_backend_refused")
        self.assertIn("Unit not found", caught.exception.detail)

    def test_an_unreachable_manager_becomes_a_structured_error(self) -> None:
        from cofferdam.workstation.errors import AdapterError

        runner = RaisingRunner(AdapterError("program timed out: systemctl"))
        supervisor = _supervisor(runner, make_project("demo"))
        with self.assertRaises(BackendUnavailable) as caught:
            supervisor.status("demo")
        self.assertEqual(caught.exception.code, "remote_control_backend_unavailable")

    def test_an_unreachable_manager_leaks_no_detail_from_the_exception(self) -> None:
        from cofferdam.workstation.errors import AdapterError

        runner = RaisingRunner(AdapterError("could not run program: /secret/path/systemctl"))
        supervisor = _supervisor(runner, make_project("demo"))
        with self.assertRaises(BackendUnavailable) as caught:
            supervisor.status("demo")
        self.assertNotIn("/secret/path", str(caught.exception.detail))
        self.assertNotIn("/secret/path", caught.exception.message)


class ErrorRedactionTests(unittest.TestCase):
    def test_stderr_is_bounded(self) -> None:
        noisy = b"x" * 5000
        runner = FakeRunner(replies=[FakeCompleted(1, b"", noisy)])
        status = SystemdUserBackend(runner).status("demo")
        self.assertLessEqual(len(status.error), systemd.MAX_ERROR_CHARS)

    def test_control_characters_are_stripped(self) -> None:
        runner = FakeRunner(replies=[FakeCompleted(1, b"", b"bad\x00\x1b[31mred\x07 thing")])
        status = SystemdUserBackend(runner).status("demo")
        for forbidden in ("\x00", "\x1b", "\x07"):
            self.assertNotIn(forbidden, status.error)

    def test_multiline_stderr_becomes_one_line(self) -> None:
        runner = FakeRunner(replies=[FakeCompleted(1, b"", b"first\nsecond\nthird")])
        status = SystemdUserBackend(runner).status("demo")
        self.assertNotIn("\n", status.error)

    def test_empty_stderr_still_produces_a_sentence(self) -> None:
        runner = FakeRunner(replies=[FakeCompleted(1, b"", b"")])
        status = SystemdUserBackend(runner).status("demo")
        self.assertTrue(status.error)
        self.assertEqual(status.state, STATE_UNKNOWN)


# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------


class StateMappingTests(unittest.TestCase):
    def test_documented_states_map_as_specified(self) -> None:
        for active_state, expected in (
            ("active", STATE_RUNNING),
            ("activating", STATE_STARTING),
            ("deactivating", STATE_STOPPING),
            ("inactive", STATE_STOPPED),
            ("failed", STATE_FAILED),
        ):
            with self.subTest(active_state=active_state):
                self.assertEqual(model.map_active_state(active_state), expected)

    def test_unrecognised_states_become_unknown(self) -> None:
        for active_state in (
            "reloading",
            "maintenance",
            "ACTIVE",
            "running",
            "",
            "   ",
            "active extra",
            None,
            1,
            True,
            [],
            {},
        ):
            with self.subTest(active_state=active_state):
                self.assertEqual(model.map_active_state(active_state), STATE_UNKNOWN)

    def test_nothing_unrecognised_ever_maps_to_running(self) -> None:
        """The one direction where a wrong answer stops the investigation."""
        for active_state in ("reloading", "maintenance", "", None, "unknown", "Active"):
            with self.subTest(active_state=active_state):
                self.assertNotEqual(model.map_active_state(active_state), STATE_RUNNING)

    def test_status_maps_each_state_end_to_end(self) -> None:
        for active_state, expected in (
            ("active", STATE_RUNNING),
            ("activating", STATE_STARTING),
            ("deactivating", STATE_STOPPING),
            ("inactive", STATE_STOPPED),
            ("failed", STATE_FAILED),
        ):
            with self.subTest(active_state=active_state):
                runner = FakeRunner(
                    replies=[FakeCompleted(0, show_output(active_state=active_state))]
                )
                self.assertEqual(SystemdUserBackend(runner).status("demo").state, expected)

    def test_an_uninstalled_template_is_unknown_not_stopped(self) -> None:
        """`LoadState=not-found` means there is no host, not that it is down."""
        runner = FakeRunner(
            replies=[FakeCompleted(0, show_output(load_state="not-found", active_state="inactive"))]
        )
        status = SystemdUserBackend(runner).status("demo")
        self.assertEqual(status.state, STATE_UNKNOWN)
        self.assertIn("not installed", status.error)

    def test_missing_properties_become_unknown(self) -> None:
        runner = FakeRunner(replies=[FakeCompleted(0, b"LoadState=loaded\n")])
        status = SystemdUserBackend(runner).status("demo")
        self.assertEqual(status.state, STATE_UNKNOWN)

    def test_malformed_output_becomes_unknown(self) -> None:
        for payload in (b"", b"garbage", b"=\n=\n", b"\x00\x01\x02"):
            with self.subTest(payload=payload):
                runner = FakeRunner(replies=[FakeCompleted(0, payload)])
                self.assertEqual(SystemdUserBackend(runner).status("demo").state, STATE_UNKNOWN)

    def test_raw_systemd_values_are_preserved(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(
                    0,
                    show_output(
                        active_state="active",
                        sub_state="running",
                        active_enter="Fri 2026-08-08 06:05:33 +03",
                    ),
                )
            ]
        )
        status = SystemdUserBackend(runner).status("demo")
        self.assertEqual(status.active_state, "active")
        self.assertEqual(status.sub_state, "running")
        self.assertEqual(status.started_at, "Fri 2026-08-08 06:05:33 +03")

    def test_no_unsupported_state_exists(self) -> None:
        """`connected`, `authenticated`, `waiting` are not in this build."""
        for forbidden in (
            "connected",
            "authenticated",
            "auth_required",
            "waiting_for_user",
            "disconnected",
        ):
            with self.subTest(state=forbidden):
                self.assertNotIn(forbidden, model.STATES)
                self.assertNotIn(forbidden, model.ACTIVE_STATE_MAP.values())

    def test_the_model_holds_no_conversation_field(self) -> None:
        status = NativeSessionStatus(project_id="demo", unit="u", state=STATE_STOPPED)
        keys = set(status.to_dict())
        for forbidden in (
            "transcript",
            "prompt",
            "answer",
            "message",
            "messages",
            "conversation",
            "history",
            "turns",
            "content",
        ):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, keys)

    def test_the_url_is_never_in_a_status_payload(self) -> None:
        """The capability material has its own route and is not in this one."""
        runner = FakeRunner(
            replies=[FakeCompleted(0, show_output(active_state="active", sub_state="running"))]
        )
        status = SystemdUserBackend(runner).status("demo")
        payload = status.to_dict()
        self.assertNotIn("session_url", payload)
        self.assertNotIn("url", payload)
        self.assertIn("url_available", payload)
        self.assertFalse(payload["url_available"])

    def test_last_seen_at_is_stamped_only_by_the_supervisor(self) -> None:
        runner = FakeRunner()
        backend_only = SystemdUserBackend(runner).status("demo")
        self.assertIsNone(backend_only.last_seen_at)

        supervised = _supervisor(FakeRunner(), make_project("demo"), clock=fixed_clock("T")).status(
            "demo"
        )
        self.assertEqual(supervised.last_seen_at, "T")


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class IdempotencyTests(unittest.TestCase):
    def test_start_on_an_active_unit_launches_nothing(self) -> None:
        runner = FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )
        supervisor = _supervisor(runner, make_project("demo"))
        status = supervisor.start("demo")
        self.assertEqual(status.state, STATE_RUNNING)
        self.assertEqual(runner.argvs_containing("start"), [], "no duplicate host")

    def test_start_on_a_starting_unit_launches_nothing(self) -> None:
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="activating")))
        supervisor = _supervisor(runner, make_project("demo"))
        self.assertEqual(supervisor.start("demo").state, STATE_STARTING)
        self.assertEqual(runner.argvs_containing("start"), [])

    def test_start_on_an_inactive_unit_does_start_it(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="inactive")),
                FakeCompleted(0),
                FakeCompleted(0, show_output(active_state="active", sub_state="running")),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        self.assertEqual(supervisor.start("demo").state, STATE_RUNNING)
        self.assertEqual(len(runner.argvs_containing("start")), 1)

    def test_start_on_a_failed_unit_retries(self) -> None:
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="failed", sub_state="failed")),
                FakeCompleted(0),
                FakeCompleted(0, show_output(active_state="active", sub_state="running")),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        self.assertEqual(supervisor.start("demo").state, STATE_RUNNING)
        self.assertEqual(len(runner.argvs_containing("start")), 1)

    def test_stop_on_an_inactive_unit_is_a_truthful_stopped(self) -> None:
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="inactive")))
        supervisor = _supervisor(runner, make_project("demo"))
        status = supervisor.stop("demo")
        self.assertEqual(status.state, STATE_STOPPED)
        self.assertIsNone(status.error)
        self.assertEqual(runner.argvs_containing("stop"), [], "nothing to stop")

    def test_repeated_starts_stay_truthful(self) -> None:
        runner = FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )
        supervisor = _supervisor(runner, make_project("demo"))
        states = [supervisor.start("demo").state for _ in range(4)]
        self.assertEqual(states, [STATE_RUNNING] * 4)
        self.assertEqual(runner.argvs_containing("start"), [])

    def test_repeated_stops_stay_truthful(self) -> None:
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="inactive")))
        supervisor = _supervisor(runner, make_project("demo"))
        states = [supervisor.stop("demo").state for _ in range(4)]
        self.assertEqual(states, [STATE_STOPPED] * 4)
        self.assertEqual(runner.argvs_containing("stop"), [])

    def test_an_unknown_state_does_not_suppress_a_start(self) -> None:
        """Unknown is not live, so a start is still attempted rather than skipped."""
        runner = FakeRunner(
            replies=[
                FakeCompleted(0, show_output(active_state="weird")),
                FakeCompleted(0),
                FakeCompleted(0, show_output(active_state="active", sub_state="running")),
            ]
        )
        supervisor = _supervisor(runner, make_project("demo"))
        supervisor.start("demo")
        self.assertEqual(len(runner.argvs_containing("start")), 1)


# ---------------------------------------------------------------------------
# Host entry point
# ---------------------------------------------------------------------------


class _RecordingConfig:
    """The two directories the entry point reads: the registry, and state."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.state_dir = config_dir / "state"


class HostEntryPointTests(unittest.TestCase):
    """The systemd ExecStart target. Never launches anything: exec is injected."""

    def setUp(self) -> None:
        self.execs: List[tuple] = []
        self.chdirs: List[str] = []
        # The entry point writes its bounded journald lines to stdout. Captured
        # here so a passing run stays readable, and kept rather than discarded
        # so the log-content assertions below have something to read.
        self._stdout = io.StringIO()
        patch = contextlib.redirect_stdout(self._stdout)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    @property
    def logged(self) -> str:
        return self._stdout.getvalue()

    def _supervise(self, argv, *, cwd, on_link, on_auth_required, on_consent_required, log):
        """Stands in for the real supervisor. Starts nothing.

        Records the argv and working directory the entry point decided on, and
        exposes the link callback so a test can simulate the child reporting a
        session without a child existing.
        """
        self.execs.append((argv[0], list(argv)))
        self.chdirs.append(cwd)
        self.on_link = on_link
        self.on_auth_required = on_auth_required
        self.on_consent_required = on_consent_required
        return 0

    def _write_registry(self, tmp: Path, **entry) -> _RecordingConfig:
        import json

        root = tmp / "project"
        root.mkdir(exist_ok=True)
        record = {"project_id": "demo", "root": str(root)}
        record.update(entry)
        (tmp / "task-projects.json").write_text(
            json.dumps({"projects": [record]}), encoding="utf-8"
        )
        return _RecordingConfig(tmp)

    def _run(self, tmp: Path, argv, **entry) -> int:
        config = self._write_registry(tmp, **entry)
        return host.main(argv, config=config, supervise=self._supervise)

    def test_it_takes_exactly_one_argument(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            for argv in ([], ["demo", "extra"], ["demo", "--permission-mode", "bypassPermissions"]):
                with self.subTest(argv=argv):
                    self.assertEqual(
                        self._run(tmp, argv, remote_control_enabled=True), host.EXIT_USAGE
                    )
            self.assertEqual(self.execs, [], "a usage error must never exec")

    def test_an_enabled_project_execs_the_fixed_argv(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fake_claude = tmp / "claude"
            fake_claude.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_claude.chmod(0o755)

            original = claude.find_executable
            claude.find_executable = lambda: fake_claude  # type: ignore[assignment]
            try:
                self._run(tmp, ["demo"], remote_control_enabled=True)
            finally:
                claude.find_executable = original  # type: ignore[assignment]

            self.assertEqual(len(self.execs), 1)
            path, argv = self.execs[0]
            self.assertEqual(path, str(fake_claude))
            self.assertEqual(
                argv,
                [
                    str(fake_claude),
                    "remote-control",
                    "--name",
                    "cofferdam-demo",
                    "--spawn",
                    "same-dir",
                ],
            )

    def test_it_changes_to_the_registered_root(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            fake_claude = tmp / "claude"
            fake_claude.write_text("#!/bin/sh\n", encoding="utf-8")
            fake_claude.chmod(0o755)

            original = claude.find_executable
            claude.find_executable = lambda: fake_claude  # type: ignore[assignment]
            try:
                self._run(tmp, ["demo"], remote_control_enabled=True)
            finally:
                claude.find_executable = original  # type: ignore[assignment]

            self.assertEqual(self.chdirs, [str(tmp / "project")])

    def test_a_disabled_capability_is_refused_without_exec(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            code = self._run(Path(directory), ["demo"])
            self.assertEqual(code, host.EXIT_REFUSED)
            self.assertEqual(self.execs, [])
            self.assertEqual(self.chdirs, [])

    def test_refusal_logging_is_bounded_and_leaks_no_path(self) -> None:
        """journald gets a sentence, not a directory listing or an environment."""
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            self._run(tmp, ["demo"])
            logged = self.logged
            self.assertIn("refused", logged)
            self.assertNotIn(str(tmp), logged, "no path reaches the journal")
            self.assertLessEqual(len(logged.splitlines()), 2)
            for line in logged.splitlines():
                self.assertLess(len(line), 300)

    def test_nothing_from_the_environment_is_logged(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            self._run(Path(directory), ["demo"])
            for value in (os.environ.get("HOME"), os.environ.get("PATH")):
                if value:
                    self.assertNotIn(value, self.logged)

    def test_an_unknown_project_is_refused_without_exec(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            code = self._run(Path(directory), ["other"], remote_control_enabled=True)
            self.assertEqual(code, host.EXIT_REFUSED)
            self.assertEqual(self.execs, [])

    def test_a_registry_disabled_project_is_refused(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            code = self._run(
                Path(directory), ["demo"], remote_control_enabled=True, enabled=False
            )
            self.assertEqual(code, host.EXIT_REFUSED)
            self.assertEqual(self.execs, [])

    def test_unsafe_identifiers_are_refused(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            for candidate in ("../../tmp", "/etc", "demo;id", "", "demo project"):
                with self.subTest(candidate=candidate):
                    code = self._run(tmp, [candidate], remote_control_enabled=True)
                    self.assertEqual(code, host.EXIT_REFUSED)
            self.assertEqual(self.execs, [])

    def test_resolve_refuses_when_claude_is_absent(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = self._write_registry(tmp, remote_control_enabled=True)
            original = claude.find_executable
            claude.find_executable = lambda: None  # type: ignore[assignment]
            try:
                with self.assertRaises(ExecutableMissing):
                    host.resolve("demo", config=config)
            finally:
                claude.find_executable = original  # type: ignore[assignment]

    def test_resolve_refuses_a_root_that_is_gone(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "task-projects.json").write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "project_id": "demo",
                                "root": str(tmp / "missing"),
                                "remote_control_enabled": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SessionRootInvalid):
                host.resolve("demo", config=_RecordingConfig(tmp))


class ClaudeVocabularyTests(unittest.TestCase):
    def test_the_argv_names_the_remote_control_subcommand(self) -> None:
        argv = claude.build_argv(Path("/usr/bin/claude"), "demo")
        self.assertEqual(argv[1], "remote-control")

    def test_no_forbidden_flag_can_appear(self) -> None:
        argv = claude.build_argv(Path("/usr/bin/claude"), "demo")
        for flag in claude.FORBIDDEN_FLAGS:
            with self.subTest(flag=flag):
                self.assertNotIn(flag, argv)

    def test_bypass_permissions_is_never_built(self) -> None:
        argv = claude.build_argv(Path("/usr/bin/claude"), "demo")
        joined = " ".join(argv)
        self.assertNotIn("bypassPermissions", joined)
        self.assertNotIn("--permission-mode", joined)
        self.assertNotIn("--dangerously", joined)

    def test_the_session_name_is_derived_from_the_project_id(self) -> None:
        self.assertEqual(claude.session_name("demo"), "cofferdam-demo")
        argv = claude.build_argv(Path("/usr/bin/claude"), "demo")
        self.assertEqual(argv[argv.index("--name") + 1], "cofferdam-demo")

    def test_the_argv_is_deterministic(self) -> None:
        first = claude.build_argv(Path("/usr/bin/claude"), "demo")
        second = claude.build_argv(Path("/usr/bin/claude"), "demo")
        self.assertEqual(first, second)

    def test_no_argument_contains_a_shell_metacharacter(self) -> None:
        argv = claude.build_argv(Path("/usr/bin/claude"), "my-project_2")
        for word in argv:
            with self.subTest(word=word):
                self.assertFalse(set(word) & set(";|&$`><\n\r\x00"))


# ---------------------------------------------------------------------------
# Unit template
# ---------------------------------------------------------------------------


class UnitTemplateTests(unittest.TestCase):
    """Static checks against the shipped template. Nothing is installed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TEMPLATE_PATH.read_text(encoding="utf-8")
        cls.directives = {}
        for line in cls.text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("["):
                continue
            key, separator, value = stripped.partition("=")
            if separator:
                cls.directives.setdefault(key.strip(), []).append(value.strip())

    def test_it_is_a_user_template_with_the_expected_name(self) -> None:
        self.assertTrue(TEMPLATE_PATH.is_file())
        self.assertEqual(TEMPLATE_PATH.name, "cofferdam-rc@.service")
        self.assertEqual(TEMPLATE_PATH.name, units.TEMPLATE_FILENAME)

    def test_it_declares_no_user_directive(self) -> None:
        """`User=` is a system-unit concept and is refused in a user unit."""
        self.assertNotIn("User", self.directives)
        self.assertNotIn("Group", self.directives)

    def test_exec_start_is_fixed_and_cofferdam_owned(self) -> None:
        exec_starts = self.directives.get("ExecStart", [])
        self.assertEqual(len(exec_starts), 1)
        command = exec_starts[0]
        self.assertIn("-m cofferdam.workstation.sessions.host", command)
        self.assertTrue(command.endswith("%i"), "the instance is the final argument")

    def test_exec_start_uses_no_shell(self) -> None:
        command = self.directives["ExecStart"][0]
        for shell_marker in ("/bin/sh", "/bin/bash", "sh -c", "bash -c", "&&", "||", ";", "|", "`", "$("):
            with self.subTest(marker=shell_marker):
                self.assertNotIn(shell_marker, command)

    def test_the_instance_is_the_raw_specifier(self) -> None:
        """`%I` and `%f` unescape `-` into `/`; `%i` does not."""
        self.assertIn("%i", self.text)
        for line in self.text.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("%I", line)
            self.assertNotIn("%f", line)

    def test_no_hardcoded_home_user_or_worktree_path(self) -> None:
        for line in self.text.splitlines():
            if line.strip().startswith("#"):
                continue
            with self.subTest(line=line):
                self.assertNotIn("/home/", line)
                self.assertNotIn("nrgis", line)
                self.assertNotIn("/root", line)
                self.assertNotIn("clones/", line)
                self.assertNotIn("worktrees/", line)

    def test_paths_are_expressed_through_the_home_specifier(self) -> None:
        self.assertIn("%h", self.directives["ExecStart"][0])
        for value in self.directives.get("WorkingDirectory", []):
            self.assertTrue(value.startswith("%h"), "no absolute host path")

    def test_the_slot_contract_matches_the_daemon_unit(self) -> None:
        """Both units must name the same A/B runtime slot.

        ``%h/cofferdam/slots/a`` is a declared deployment contract, not a local
        path: DESIGN.md defines the slot layout, docs/host-setup.md installs
        into it, and docs/SERVICE_LIFECYCLE.md documents ExecStart pointing
        there. Asserting the two units agree is what makes an A/B slot switch
        move the daemon and its Remote Control hosts together, rather than
        leaving them on different builds of the same package.
        """
        base = (REPO_ROOT / "deploy" / "cofferdam-workstation.service").read_text(
            encoding="utf-8"
        )
        base_workdir = [
            line.split("=", 1)[1].strip()
            for line in base.splitlines()
            if line.strip().startswith("WorkingDirectory=")
        ]
        self.assertEqual(self.directives["WorkingDirectory"], base_workdir)

    def test_the_interpreter_is_the_slot_venv_used_by_the_daemon(self) -> None:
        """Same interpreter as the base unit, so both run the same install."""
        base = (REPO_ROOT / "deploy" / "cofferdam-workstation.service").read_text(
            encoding="utf-8"
        )
        base_exec = [
            line.split("=", 1)[1].strip()
            for line in base.splitlines()
            if line.strip().startswith("ExecStart=") and line.strip() != "ExecStart="
        ]
        self.assertTrue(base_exec)
        base_interpreter = base_exec[0].split()[0]
        self.assertEqual(self.directives["ExecStart"][0].split()[0], base_interpreter)

    def test_the_invoked_package_is_shipped_by_the_distribution(self) -> None:
        """A wheel install must contain the module ExecStart names.

        The regression this guards actually happened: `cofferdam.workstation.
        tasks` shipped in M2F and was never added to pyproject's package list,
        so a built wheel omitted it entirely. The documented install is editable
        and hid that for two milestones.
        """
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for required in (
            '"cofferdam.workstation.sessions"',
            '"cofferdam.workstation.tasks"',
        ):
            with self.subTest(package=required):
                self.assertIn(required, pyproject)

    def test_it_carries_no_secret(self) -> None:
        lowered = self.text.lower()
        for marker in ("token", "secret", "password", "api_key", "apikey", "bearer"):
            with self.subTest(marker=marker):
                for line in self.text.splitlines():
                    if line.strip().startswith("#"):
                        continue
                    self.assertNotIn(marker, line.lower())
        self.assertNotIn("ts.net", lowered)

    def test_it_accepts_no_caller_supplied_environment_file(self) -> None:
        self.assertNotIn("EnvironmentFile", self.directives)

    def test_restart_policy_is_on_failure_and_bounded(self) -> None:
        self.assertEqual(self.directives.get("Restart"), ["on-failure"])
        restart_sec = int(self.directives["RestartSec"][0])
        self.assertGreater(restart_sec, 0)
        self.assertLessEqual(restart_sec, 60)

    def test_the_start_rate_limit_is_bounded(self) -> None:
        self.assertIn("StartLimitIntervalSec", self.directives)
        self.assertIn("StartLimitBurst", self.directives)
        self.assertGreater(int(self.directives["StartLimitBurst"][0]), 0)

    def test_output_goes_to_the_journal(self) -> None:
        self.assertEqual(self.directives.get("StandardOutput"), ["journal"])
        self.assertEqual(self.directives.get("StandardError"), ["journal"])

    def test_shutdown_is_process_group_safe_and_bounded(self) -> None:
        self.assertEqual(self.directives.get("KillMode"), ["mixed"])
        self.assertEqual(self.directives.get("KillSignal"), ["SIGTERM"])
        stop_timeout = int(self.directives["TimeoutStopSec"][0])
        self.assertGreater(stop_timeout, 0)
        self.assertLessEqual(stop_timeout, 120)

    def test_home_and_network_are_left_reachable(self) -> None:
        """Claude's login lives under HOME and Remote Control needs the network."""
        for over_aggressive in (
            "ProtectHome",
            "ProtectSystem",
            "PrivateNetwork",
            "IPAddressDeny",
            "RestrictAddressFamilies",
            "PrivateUsers",
            "DynamicUser",
        ):
            with self.subTest(directive=over_aggressive):
                self.assertNotIn(over_aggressive, self.directives)

    def test_it_is_not_installed_or_enabled_by_this_repository(self) -> None:
        """No [Install] section, so nothing can enable it by accident.

        Checked against real section headers rather than the raw text: the
        template *discusses* why the section is absent, and a comment saying so
        must not be mistaken for the section itself.
        """
        sections = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("[") and not line.strip().startswith("#")
        ]
        self.assertEqual(sections, ["[Unit]", "[Service]"])
        self.assertNotIn("WantedBy", self.directives)
        self.assertNotIn("RequiredBy", self.directives)

    def test_it_names_no_graphical_target(self) -> None:
        """The M1.1 login-loop class of mistake, kept out of the new unit too."""
        self.assertNotIn("graphical-session.target", self.text)


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------


class NoLiveEffectTests(unittest.TestCase):
    """Asserted against the source, so a future edit cannot quietly undo them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.package = REPO_ROOT / "cofferdam" / "workstation" / "sessions"
        cls.sources = sorted(cls.package.rglob("*.py"))

    def test_the_package_uses_no_shell(self) -> None:
        for path in self.sources:
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("shell=True", source)
                self.assertNotIn("os.system", source)
                self.assertNotIn("os.popen", source)

    def test_only_the_wrapper_touches_subprocess(self) -> None:
        """Process control is one file, named here so a second one fails.

        M2H PR2 needs to read the child's stdout to capture the session link, so
        the package can no longer be subprocess-free the way PR1 was. It is
        confined to `wrapper.py` instead: the supervisor, the model, the state
        store and the systemd backend still cannot reach a process, and the
        systemd backend still goes through the shared `run_fixed` helper.
        """
        offenders = [
            path.name
            for path in self.sources
            if path.name != "wrapper.py" and "subprocess." in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], "subprocess outside the wrapper: %s" % offenders)

    def test_the_package_never_names_the_system_scope(self) -> None:
        for path in self.sources:
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                if line.strip().startswith("#") or line.strip().startswith("*"):
                    continue
                with self.subTest(module=path.name, line=line):
                    self.assertNotIn('"--system"', line)
                    self.assertNotIn("'--system'", line)

    def test_the_package_reads_no_transcript_or_journal(self) -> None:
        """No module names a journal command or a transcript file.

        Checked against the *string literals* the code can actually act on, not
        the raw text — several modules explain in prose that they deliberately
        read no journal, and prohibiting the word would punish the explanation
        rather than the behaviour.
        """
        for path in self.sources:
            for literal in _string_literals(path):
                with self.subTest(module=path.name, literal=literal):
                    lowered = literal.lower()
                    self.assertNotIn("journalctl", lowered)
                    self.assertNotIn(".jsonl", lowered)
                    self.assertNotIn(".claude/projects", lowered)
                    self.assertNotIn("transcript", lowered)

    def test_the_supervisor_does_not_import_task_core(self) -> None:
        """Lane A depends on the project registry, never on Task Core itself."""
        for path in self.sources:
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "tasks.service",
                "tasks.store",
                "tasks.adapters",
                "tasks.models",
                "tasks.observe",
            ):
                with self.subTest(module=path.name, imports=forbidden):
                    self.assertNotIn("from ..%s import" % forbidden, source)
                    self.assertNotIn("import cofferdam.workstation.%s" % forbidden, source)

    def test_every_remote_control_route_requires_authentication(self) -> None:
        """PR1 asserted there were no routes. PR2 adds four, all authenticated.

        Checked against the source rather than a live app so this runs on the
        stdlib-only CI path: every `@app.<verb>("/api/remote-control...")`
        decorator must carry `dependencies=[Depends(require_token)]`.
        """
        service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        decorators = re.findall(
            r'@app\.(?:get|post|put|delete)\(\s*"(/api/remote-control[^"]*)"([^)]*)\)',
            service,
        )
        self.assertEqual(
            sorted(path for path, _ in decorators),
            [
                "/api/remote-control/{project_id}",
                "/api/remote-control/{project_id}/link",
                "/api/remote-control/{project_id}/start",
                "/api/remote-control/{project_id}/stop",
            ],
        )
        for path, rest in decorators:
            with self.subTest(route=path):
                self.assertIn("require_token", rest)

    def test_no_route_takes_a_path_unit_or_flag_from_the_caller(self) -> None:
        """The only path parameter is a project id."""
        service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )
        block = service[service.index("native Remote Control (M2H") :]
        block = block[: block.index("-- live events")]
        # Comments and docstrings explain what is *not* accepted; matching them
        # would fail the explanation rather than the code.
        block = "\n".join(
            line for line in block.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in (
            "{unit}",
            "{path}",
            "{root}",
            "permission_mode",
            "bypassPermissions",
            "executable",
            "argv",
            "systemctl",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, block)


if __name__ == "__main__":
    unittest.main()
