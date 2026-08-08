"""M2H PR2 — link capture, runtime state, the supervising wrapper and routes.

Standard-library only, so it runs on the stdlib-only CI path.

No test here starts a Remote Control host, runs ``systemctl``, reaches the
network, requires a Claude login or touches the live registry. The wrapper takes
an injected ``popen``; the supervisor takes an injected runner and store; the
entry point takes an injected supervisor. The one place a real child process is
started is :class:`WrapperSignalTests`, which runs ``/bin/sh`` fixtures that
print fake output and sleep — never Claude.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cofferdam.workstation.sessions import links, state, wrapper
from cofferdam.workstation.sessions.errors import (
    LinkUnavailable,
    SessionProjectUnknown,
    StateUnavailable,
)
from cofferdam.workstation.sessions.model import (
    STATE_FAILED,
    STATE_RUNNING,
    STATE_STOPPED,
)
from cofferdam.workstation.sessions.state import LinkStore, new_generation
from cofferdam.workstation.sessions.supervisor import RemoteControlSupervisor
from cofferdam.workstation.sessions.systemd import SystemdUserBackend

from ._sessions_doubles import (
    FakeCompleted,
    FakeRunner,
    MemoryLinkStore,
    fixed_clock,
    make_project,
    provider,
    show_output,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: A stand-in with the shape the recogniser expects. Not a real session URL —
#: the real format is confirmed during the live spike, and until then this
#: exercises the machinery without asserting a format nobody has observed.
SAMPLE_LINK = "https://claude.ai/code/session/abc123XYZ-placeholder"


class _Config:
    def __init__(self, home: Path) -> None:
        self.state_dir = home / "state"


def _store(home: Path) -> LinkStore:
    return LinkStore(_Config(home))


# ---------------------------------------------------------------------------
# Link recognition and redaction
# ---------------------------------------------------------------------------


class LinkRecognitionTests(unittest.TestCase):
    def test_a_claude_link_is_recognised(self) -> None:
        self.assertEqual(links.find_link("Open " + SAMPLE_LINK + " to connect"), SAMPLE_LINK)

    def test_unrelated_output_is_ignored(self) -> None:
        for line in (
            "Starting Remote Control...",
            "Waiting for connections",
            "error: something went wrong",
            "",
            "see the docs for help",
            "1 session ready",
        ):
            with self.subTest(line=line):
                self.assertIsNone(links.find_link(line))

    def test_a_link_on_another_host_is_not_accepted(self) -> None:
        """Host allowlist: a URL the child prints elsewhere is not a session."""
        for url in (
            "https://example.com/code/session/abc",
            "https://evil.claude.ai.attacker.test/code/x",
            "http://claude.ai/code/session/abc",
        ):
            with self.subTest(url=url):
                self.assertIsNone(links.find_link(url))

    def test_an_over_long_candidate_is_refused(self) -> None:
        self.assertIsNone(links.find_link("https://claude.ai/code/" + "a" * 900))

    def test_the_format_is_marked_unconfirmed(self) -> None:
        """Honest until the live spike says otherwise."""
        self.assertFalse(links.LINK_FORMAT_CONFIRMED)


class ChunkedCaptureTests(unittest.TestCase):
    def test_a_link_split_across_chunks_is_found(self) -> None:
        scanner = links.LinkScanner()
        half = len(SAMPLE_LINK) // 2
        self.assertIsNone(scanner.feed("noise " + SAMPLE_LINK[:half]))
        self.assertEqual(scanner.feed(SAMPLE_LINK[half:] + " more\n"), SAMPLE_LINK)

    def test_a_partial_link_is_never_reported_as_complete(self) -> None:
        """The dangerous case: a chunk that ends mid-URL still matches a pattern.

        `https://claude.ai/code/` is itself a plausible match, so a scanner that
        searched each chunk would store a truncated capability URL and call it a
        success. Nothing may be emitted until a newline proves the line ended.
        """
        scanner = links.LinkScanner()
        for cut in range(20, len(SAMPLE_LINK)):
            probe = links.LinkScanner()
            self.assertIsNone(probe.feed(SAMPLE_LINK[:cut]))
            self.assertIsNone(probe.link)
        self.assertIsNone(scanner.link)

    def test_a_link_split_three_ways_is_found(self) -> None:
        scanner = links.LinkScanner()
        third = len(SAMPLE_LINK) // 3
        scanner.feed(SAMPLE_LINK[:third])
        scanner.feed(SAMPLE_LINK[third : 2 * third])
        self.assertEqual(scanner.feed(SAMPLE_LINK[2 * third :] + "\n"), SAMPLE_LINK)

    def test_only_the_first_link_is_kept(self) -> None:
        scanner = links.LinkScanner()
        self.assertEqual(scanner.feed(SAMPLE_LINK + "\n"), SAMPLE_LINK)
        self.assertIsNone(scanner.feed("https://claude.ai/code/session/second\n"))
        self.assertEqual(scanner.link, SAMPLE_LINK)

    def test_the_carry_buffer_is_bounded(self) -> None:
        """A chatty child cannot make this allocate without limit."""
        scanner = links.LinkScanner()
        for _ in range(200):
            scanner.feed("x" * 4096)
        self.assertLessEqual(len(scanner._buffer), scanner.MAX_BUFFER)


class RedactionTests(unittest.TestCase):
    def test_a_link_is_removed(self) -> None:
        redacted = links.redact("Connect at " + SAMPLE_LINK + " now")
        self.assertNotIn(SAMPLE_LINK, redacted)
        self.assertIn(links.REDACTION, redacted)

    def test_redaction_is_wider_than_recognition(self) -> None:
        """Any URL goes, not only ones the recogniser understands.

        A redactor that hides only what it already parses leaks the first time
        the format changes — which is the one moment it matters most.
        """
        for url in (
            "https://example.com/whatever?token=secret",
            "http://localhost:9999/x",
            "https://claude.ai.new-format.test/abc",
        ):
            with self.subTest(url=url):
                self.assertNotIn(url, links.redact("see " + url))

    def test_query_material_cannot_survive(self) -> None:
        text = "url=" + SAMPLE_LINK + "?token=SUPERSECRET&k=v"
        redacted = links.redact(text)
        self.assertNotIn("SUPERSECRET", redacted)

    def test_control_characters_are_stripped_and_lines_collapsed(self) -> None:
        redacted = links.redact("a\x1b[31mb\nc\x00d")
        self.assertNotIn("\x1b", redacted)
        self.assertNotIn("\n", redacted)
        self.assertNotIn("\x00", redacted)

    def test_retained_lines_are_bounded_and_redacted(self) -> None:
        lines = ["line %d %s" % (i, SAMPLE_LINK) for i in range(100)]
        out = links.redact_lines(lines, 5)
        self.assertEqual(len(out), 5)
        for line in out:
            self.assertNotIn("claude.ai", line)
            self.assertLessEqual(len(line), 200)


# ---------------------------------------------------------------------------
# Runtime state store
# ---------------------------------------------------------------------------


class LinkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.store = _store(self.home)

    def test_a_written_link_reads_back_for_its_generation(self) -> None:
        generation = new_generation()
        self.store.write("demo", generation=generation, link=SAMPLE_LINK)
        document = self.store.read_link("demo", generation=generation)
        self.assertIsNotNone(document)
        self.assertEqual(document["link"], SAMPLE_LINK)

    def test_an_older_generation_cannot_read_the_link(self) -> None:
        """Restart invalidates the previous URL structurally."""
        self.store.write("demo", generation="gen-new", link=SAMPLE_LINK)
        self.assertIsNone(self.store.read_link("demo", generation="gen-old"))

    def test_a_new_generation_replaces_the_old_link(self) -> None:
        self.store.write("demo", generation="gen-1", link=SAMPLE_LINK)
        self.store.write("demo", generation="gen-2")
        self.assertIsNone(self.store.read_link("demo", generation="gen-1"))
        self.assertIsNone(self.store.read_link("demo", generation="gen-2"))

    def test_clear_removes_the_link(self) -> None:
        self.store.write("demo", generation="g", link=SAMPLE_LINK)
        self.store.clear("demo")
        self.assertIsNone(self.store.read("demo"))

    def test_clearing_nothing_is_not_an_error(self) -> None:
        self.store.clear("never-started")

    def test_the_file_is_owner_only(self) -> None:
        self.store.write("demo", generation="g", link=SAMPLE_LINK)
        mode = stat.S_IMODE(os.stat(self.store.path_for("demo")).st_mode)
        self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR, oct(mode))

    def test_the_directory_is_owner_only(self) -> None:
        self.store.write("demo", generation="g", link=SAMPLE_LINK)
        mode = stat.S_IMODE(os.stat(self.store.directory).st_mode)
        self.assertEqual(mode, stat.S_IRWXU, oct(mode))

    def test_the_write_is_atomic(self) -> None:
        """No temp file survives, and the destination is replaced whole."""
        self.store.write("demo", generation="g", link=SAMPLE_LINK)
        self.store.write("demo", generation="g2", link=SAMPLE_LINK)
        leftovers = [p.name for p in self.store.directory.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertEqual(
            json.loads(self.store.path_for("demo").read_text())["generation"], "g2"
        )

    def test_projects_are_isolated(self) -> None:
        self.store.write("alpha", generation="g", link=SAMPLE_LINK)
        self.store.write("beta", generation="g", link="https://claude.ai/code/session/beta")
        self.assertEqual(self.store.read("alpha")["link"], SAMPLE_LINK)
        self.assertNotEqual(self.store.read("beta")["link"], SAMPLE_LINK)

    def test_an_unsafe_project_id_cannot_name_a_path(self) -> None:
        for candidate in ("../../secrets/token", "/etc/passwd", "", "a b", "A"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(SessionProjectUnknown):
                    self.store.path_for(candidate)

    def test_a_symlinked_state_directory_is_refused(self) -> None:
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        (self.home / "state").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(StateUnavailable):
            self.store.write("demo", generation="g", link=SAMPLE_LINK)

    def test_a_symlinked_state_file_is_refused(self) -> None:
        self.store._ensure_directory()
        target = self.home / "target.json"
        target.write_text("{}", encoding="utf-8")
        self.store.path_for("demo").symlink_to(target)
        with self.assertRaises(StateUnavailable):
            self.store.write("demo", generation="g", link=SAMPLE_LINK)

    def test_a_corrupt_file_reads_as_absent(self) -> None:
        self.store._ensure_directory()
        self.store.path_for("demo").write_text("not json", encoding="utf-8")
        self.assertIsNone(self.store.read("demo"))

    def test_a_file_naming_a_different_project_is_not_trusted(self) -> None:
        self.store._ensure_directory()
        self.store.path_for("demo").write_text(
            json.dumps({"project_id": "other", "generation": "g", "link": SAMPLE_LINK}),
            encoding="utf-8",
        )
        self.assertIsNone(self.store.read("demo"))

    def test_an_oversized_document_is_refused(self) -> None:
        with self.assertRaises(StateUnavailable):
            self.store.write("demo", generation="g", link="x" * (state.MAX_STATE_BYTES + 10))

    def test_generations_are_unique(self) -> None:
        self.assertEqual(len({new_generation() for _ in range(200)}), 200)


# ---------------------------------------------------------------------------
# The supervising wrapper
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, lines, status=0):
        import io

        self.stdout = io.BytesIO(b"".join(line.encode() for line in lines))
        self.pid = os.getpid()
        self._status = status
        self.signals = []

    def wait(self, timeout=None):
        return self._status

    def send_signal(self, number):
        self.signals.append(number)


class WrapperTests(unittest.TestCase):
    def _run(self, lines, status=0):
        captured = {}
        recorded = []

        def popen(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            return _FakeProcess(lines, status)

        host = wrapper.SupervisedHost(
            ["/usr/bin/claude", "remote-control", "--name", "cofferdam-demo"],
            cwd="/srv/demo",
            on_link=lambda link: recorded.append(link),
            log=lambda message: None,
            popen=popen,
        )
        code = host.run()
        return code, captured, recorded, host

    def test_the_argv_is_passed_through_unchanged_and_as_a_list(self) -> None:
        _, captured, _, _ = self._run(["ready\n"])
        self.assertEqual(
            captured["argv"],
            ["/usr/bin/claude", "remote-control", "--name", "cofferdam-demo"],
        )

    def test_no_shell_is_used(self) -> None:
        _, captured, _, _ = self._run(["ready\n"])
        self.assertNotIn("shell", captured["kwargs"])
        self.assertTrue(captured["kwargs"]["start_new_session"])
        self.assertEqual(captured["kwargs"]["cwd"], "/srv/demo")

    def test_stdin_is_closed_so_nothing_can_be_injected(self) -> None:
        """No prompt channel into a native session, structurally."""
        _, captured, _, _ = self._run(["ready\n"])
        self.assertEqual(captured["kwargs"]["stdin"], subprocess.DEVNULL)

    def test_a_link_in_the_stream_is_reported_once(self) -> None:
        _, _, recorded, _ = self._run(["starting\n", SAMPLE_LINK + "\n", "more\n"])
        self.assertEqual(recorded, [SAMPLE_LINK])

    def test_a_second_link_does_not_replace_the_first(self) -> None:
        _, _, recorded, _ = self._run(
            [SAMPLE_LINK + "\n", "https://claude.ai/code/session/other\n"]
        )
        self.assertEqual(recorded, [SAMPLE_LINK])

    def test_retained_output_is_redacted(self) -> None:
        _, _, _, host = self._run(["starting\n", SAMPLE_LINK + "\n"])
        joined = " ".join(host.retained)
        self.assertNotIn(SAMPLE_LINK, joined)
        self.assertNotIn("claude.ai", joined)

    def test_retained_output_is_bounded(self) -> None:
        _, _, _, host = self._run(["line %d\n" % i for i in range(500)])
        self.assertLessEqual(len(host.retained), wrapper.MAX_RETAINED_LINES)

    def test_a_very_long_line_is_truncated(self) -> None:
        _, _, _, host = self._run(["x" * 100000 + "\n"])
        for line in host.retained:
            self.assertLessEqual(len(line), wrapper.MAX_LINE_CHARS)

    def test_the_child_exit_status_is_returned(self) -> None:
        for status in (0, 1, 2, 42):
            with self.subTest(status=status):
                code, _, _, _ = self._run(["x\n"], status=status)
                self.assertEqual(code, status)

    def test_a_signalled_child_reports_128_plus_signal(self) -> None:
        code, _, _, _ = self._run(["x\n"], status=-signal.SIGTERM)
        self.assertEqual(code, 128 + int(signal.SIGTERM))

    def test_auth_detection_is_off_until_confirmed(self) -> None:
        """No auth_required until a real signal has been observed."""
        self.assertFalse(wrapper.AUTH_FORMAT_CONFIRMED)
        for line in ("please log in", "you must authenticate", "subscription required"):
            with self.subTest(line=line):
                self.assertFalse(wrapper.detect_auth_required(line))

    def test_nothing_from_the_environment_is_retained(self) -> None:
        _, _, _, host = self._run(["HOME=%s\n" % os.environ.get("HOME", "/root")])
        # The line is retained verbatim only because the child printed it; what
        # matters is that the wrapper never *adds* environment of its own.
        self.assertEqual(len(host.retained), 1)


class WrapperSignalTests(unittest.TestCase):
    """The one place a real child runs. It is /bin/sh, never Claude."""

    def test_terminate_kills_the_whole_process_group(self) -> None:
        script = "sh -c 'sleep 120 & sleep 120'"
        host = wrapper.SupervisedHost(
            ["/bin/sh", "-c", "sleep 120 & sleep 120"],
            cwd="/tmp",
            log=lambda message: None,
        )

        import threading

        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("code", host.run()))
        thread.start()
        for _ in range(100):
            if host._process is not None:
                break
            import time

            time.sleep(0.02)
        self.assertIsNotNone(host._process, "child never started: " + script)

        group = os.getpgid(host._process.pid)
        host.terminate()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "the wrapper did not return after terminate")

        # The group is gone: signalling it now raises rather than reaching a
        # surviving grandchild.
        with self.assertRaises(ProcessLookupError):
            for _ in range(50):
                os.killpg(group, 0)
                import time

                time.sleep(0.05)

    def test_a_real_child_link_is_captured_and_redacted(self) -> None:
        recorded = []
        retained = []
        host = wrapper.SupervisedHost(
            ["/bin/sh", "-c", "echo starting; echo " + SAMPLE_LINK + "; echo done"],
            cwd="/tmp",
            on_link=recorded.append,
            log=retained.append,
        )
        code = host.run()
        self.assertEqual(code, 0)
        self.assertEqual(recorded, [SAMPLE_LINK])
        self.assertNotIn(SAMPLE_LINK, " ".join(host.retained))
        self.assertNotIn(SAMPLE_LINK, " ".join(retained))


# ---------------------------------------------------------------------------
# Health and link evidence through the supervisor
# ---------------------------------------------------------------------------


def _supervisor(runner, *projects, store=None):
    return RemoteControlSupervisor(
        provider(*projects),
        backend=SystemdUserBackend(runner),
        store=store if store is not None else MemoryLinkStore(),
        clock=fixed_clock(),
    )


class HealthEvidenceTests(unittest.TestCase):
    def _running(self):
        return FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )

    def test_a_running_host_without_a_link_is_not_connected(self) -> None:
        supervisor = _supervisor(self._running(), make_project("demo"))
        status = supervisor.status("demo")
        self.assertEqual(status.state, STATE_RUNNING)
        self.assertFalse(status.url_available)
        self.assertFalse(status.auth_required)

    def test_a_captured_link_does_not_imply_authenticated(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        status = _supervisor(self._running(), make_project("demo"), store=store).status("demo")
        self.assertTrue(status.url_available)
        self.assertFalse(status.auth_required)
        self.assertEqual(status.state, STATE_RUNNING)

    def test_no_connected_or_authenticated_state_exists(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        payload = _supervisor(self._running(), make_project("demo"), store=store).status(
            "demo"
        ).to_dict()
        for forbidden in ("connected", "authenticated", "user_present", "conversation_active"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, payload)
        self.assertNotIn(payload["state"], ("connected", "authenticated"))

    def test_link_evidence_is_ignored_when_the_unit_is_not_live(self) -> None:
        """A state file outliving its process must not claim a live URL."""
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        for active_state, expected in (("inactive", STATE_STOPPED), ("failed", STATE_FAILED)):
            with self.subTest(active_state=active_state):
                runner = FakeRunner(
                    default=FakeCompleted(0, show_output(active_state=active_state))
                )
                status = _supervisor(runner, make_project("demo"), store=store).status("demo")
                self.assertEqual(status.state, expected)
                self.assertFalse(status.url_available)

    def test_the_status_payload_never_carries_the_url(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        payload = _supervisor(self._running(), make_project("demo"), store=store).status(
            "demo"
        ).to_dict()
        self.assertNotIn(SAMPLE_LINK, json.dumps(payload))
        self.assertTrue(payload["url_available"])

    def test_status_survives_an_unreadable_state_store(self) -> None:
        class Broken:
            def read(self, project_id):
                raise StateUnavailable()

            def clear(self, project_id):
                raise StateUnavailable()

        status = _supervisor(self._running(), make_project("demo"), store=Broken()).status(
            "demo"
        )
        self.assertEqual(status.state, STATE_RUNNING)
        self.assertIsNotNone(status.error)


class LinkRetrievalTests(unittest.TestCase):
    def _running(self):
        return FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )

    def test_the_link_is_returned_for_a_live_generation(self) -> None:
        store = MemoryLinkStore(
            {"demo": {"generation": "g1", "link": SAMPLE_LINK, "discovered_at": "T"}}
        )
        payload = _supervisor(self._running(), make_project("demo"), store=store).link("demo")
        self.assertEqual(payload["url"], SAMPLE_LINK)
        self.assertEqual(payload["generation"], "g1")

    def test_no_link_before_capture(self) -> None:
        supervisor = _supervisor(self._running(), make_project("demo"))
        with self.assertRaises(LinkUnavailable):
            supervisor.link("demo")

    def test_no_link_when_the_host_is_stopped(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="inactive")))
        with self.assertRaises(LinkUnavailable):
            _supervisor(runner, make_project("demo"), store=store).link("demo")

    def test_no_link_when_the_host_failed(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="failed")))
        with self.assertRaises(LinkUnavailable):
            _supervisor(runner, make_project("demo"), store=store).link("demo")

    def test_a_link_cannot_be_read_across_projects(self) -> None:
        store = MemoryLinkStore({"alpha": {"generation": "g", "link": SAMPLE_LINK}})
        supervisor = _supervisor(
            self._running(), make_project("alpha"), make_project("beta"), store=store
        )
        self.assertEqual(supervisor.link("alpha")["url"], SAMPLE_LINK)
        with self.assertRaises(LinkUnavailable):
            supervisor.link("beta")

    def test_an_unknown_project_cannot_retrieve_a_link(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        supervisor = _supervisor(self._running(), make_project("demo"), store=store)
        with self.assertRaises(SessionProjectUnknown):
            supervisor.link("nope")

    def test_stop_clears_the_link(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        runner = FakeRunner(
            default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
        )
        supervisor = _supervisor(runner, make_project("demo"), store=store)
        supervisor.stop("demo")
        self.assertIn("demo", store.cleared)
        self.assertIsNone(store.read("demo"))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditTests(unittest.TestCase):
    def _store(self):
        from cofferdam.workstation.store import ActionStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        class _C:
            home = Path(tmp.name)
            max_action_records = 50

            @property
            def actions_path(self):
                return Path(tmp.name) / "actions.json"

        return ActionStore(_C())

    def test_the_audit_signature_cannot_accept_a_url(self) -> None:
        """Structural: there is no parameter a link could be passed through."""
        import inspect

        from cofferdam.workstation.store import ActionStore

        parameters = set(
            inspect.signature(ActionStore.record_remote_control_event).parameters
        )
        for forbidden in ("url", "link", "session_url", "output", "detail", "content"):
            with self.subTest(parameter=forbidden):
                self.assertNotIn(forbidden, parameters)

    def test_a_recorded_event_contains_no_url(self) -> None:
        store = self._store()
        store.record_remote_control_event(
            "remote_control.link_retrieved",
            "ok",
            project_id="demo",
            unit="cofferdam-rc@demo.service",
            generation="g1",
            state="running",
        )
        serialised = json.dumps(store.recent(10))
        self.assertNotIn("claude.ai", serialised)
        self.assertNotIn(SAMPLE_LINK, serialised)
        self.assertIn("remote_control.link_retrieved", serialised)
        self.assertIn("g1", serialised)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class RouteShapeTests(unittest.TestCase):
    """Source-level route assertions, so this runs without FastAPI installed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.service = (REPO_ROOT / "cofferdam" / "workstation" / "service.py").read_text(
            encoding="utf-8"
        )

    def test_the_link_route_is_separate_from_status(self) -> None:
        self.assertIn('"/api/remote-control/{project_id}/link"', self.service)
        self.assertIn('"/api/remote-control/{project_id}"', self.service)

    def test_the_status_route_returns_to_dict_which_drops_the_url(self) -> None:
        from cofferdam.workstation.sessions.model import NativeSessionStatus

        payload = NativeSessionStatus(
            project_id="demo", unit="u", state=STATE_RUNNING, session_url=SAMPLE_LINK
        ).to_dict()
        self.assertNotIn(SAMPLE_LINK, json.dumps(payload))

    def test_every_route_is_token_protected(self) -> None:
        decorators = re.findall(
            r'@app\.(?:get|post)\(\s*"(/api/remote-control[^"]*)"([^)]*)\)', self.service
        )
        self.assertEqual(len(decorators), 4)
        for path, rest in decorators:
            with self.subTest(route=path):
                self.assertIn("require_token", rest)

    def test_the_refusal_codes_map_to_sensible_statuses(self) -> None:
        """Read from source, so this runs on the stdlib-only path too."""
        block = self.service[self.service.index("_REMOTE_CONTROL_STATUS = {") :]
        block = block[: block.index("}")]
        for code, status in (
            ("CODE_RC_PROJECT_UNKNOWN", "404"),
            ("CODE_RC_PROJECT_DISABLED", "409"),
            ("CODE_NOT_ENABLED", "409"),
            ("CODE_LINK_UNAVAILABLE", "409"),
            ("CODE_BACKEND_UNAVAILABLE", "503"),
        ):
            with self.subTest(code=code):
                self.assertIn(code + ": " + status, block)


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (REPO_ROOT / "deploy" / "cofferdam-rc@.service").read_text(encoding="utf-8")

    def test_the_template_still_has_no_install_section(self) -> None:
        sections = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("[") and not line.strip().startswith("#")
        ]
        self.assertEqual(sections, ["[Unit]", "[Service]"])

    def test_no_user_directive_and_no_hardcoded_home(self) -> None:
        for line in self.text.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertFalse(line.strip().startswith("User="))
            self.assertNotIn("/home/", line)
            self.assertNotIn("nrgis", line)


if __name__ == "__main__":
    unittest.main()
