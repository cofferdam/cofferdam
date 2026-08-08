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

import contextlib
import io
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

from cofferdam.workstation.sessions import links, state, supervisor as supervisor_module, systemd, wrapper
from cofferdam.workstation.sessions.errors import (
    LinkUnavailable,
    SessionProjectUnknown,
    StateUnavailable,
)
from cofferdam.workstation.sessions.model import (
    STATE_FAILED,
    STATE_RUNNING,
    STATE_STOPPED,
    NativeSessionStatus,
    map_active_state,
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

#: A link with the **confirmed live structure** and entirely **fake** capability
#: material. The shape — https, allowlisted host, ``/code``, one ``environment``
#: query parameter, a 28-character URL-safe token — is what M2H PR2.5 observed
#: twice on a real PTY. The token is typed here by hand and grants nothing.
#:
#: Real capability material never enters this file, and a test below asserts the
#: fixture is not mistakable for a live one.
SAMPLE_TOKEN = "FAKEfake0123456789-_TESTtok0"
SAMPLE_LINK = "https://claude.ai/code?environment=" + SAMPLE_TOKEN


class _Config:
    def __init__(self, home: Path) -> None:
        self.state_dir = home / "state"


def _store(home: Path) -> LinkStore:
    return LinkStore(_Config(home))


@contextlib.contextmanager
def confirmed_format():
    """The gate as this build ships it: open, against the confirmed format.

    Kept as an explicit context manager even though ``True`` is now the default,
    so the tests that depend on recognition say so at the point they depend on
    it. If the gate is ever closed again — a workstation where the format is in
    doubt, a build that has not re-confirmed it — these tests keep testing the
    thing they were written to test instead of silently passing on a closed
    gate.
    """
    original = links.LINK_FORMAT_CONFIRMED
    links.LINK_FORMAT_CONFIRMED = True
    try:
        yield
    finally:
        links.LINK_FORMAT_CONFIRMED = original


@contextlib.contextmanager
def unconfirmed_format():
    """The fail-closed half of the gate, which still has to work.

    M2H PR2.5 confirmed the format, so the shipped default is now ``True``. That
    makes the closed behaviour *harder* to keep honest, not less important: it is
    the mode a future CLI change drops us back into, and the whole design rests
    on "no link at all" being the answer when the format is not trusted. So it
    is exercised deliberately here rather than being whatever the module happens
    to be set to.
    """
    original = links.LINK_FORMAT_CONFIRMED
    links.LINK_FORMAT_CONFIRMED = False
    try:
        yield
    finally:
        links.LINK_FORMAT_CONFIRMED = original


# ---------------------------------------------------------------------------
# Link recognition and redaction
# ---------------------------------------------------------------------------


class LinkRecognitionTests(unittest.TestCase):
    def test_a_claude_link_is_recognised_once_the_format_is_confirmed(self) -> None:
        with confirmed_format():
            self.assertEqual(
                links.find_link("Open " + SAMPLE_LINK + " to connect"), SAMPLE_LINK
            )

    def test_nothing_is_recognised_while_the_format_is_unconfirmed(self) -> None:
        """The gate, at its source. Fail-closed, not informational.

        A link of the *confirmed* shape, refused anyway, because the gate is
        about trust in the format rather than the look of the string.
        """
        with unconfirmed_format():
            self.assertIsNone(links.find_link("Open " + SAMPLE_LINK + " to connect"))

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

    def test_the_format_is_confirmed_in_this_build(self) -> None:
        """M2H PR2.5 observed it live, twice, so the gate ships open."""
        self.assertTrue(links.LINK_FORMAT_CONFIRMED)

    def test_the_confirmed_pattern_is_the_observed_structure(self) -> None:
        """Pinned to what was seen, not to whatever still parses."""
        self.assertEqual(links.LINK_PATH, "/code")
        self.assertEqual(links.LINK_QUERY_KEY, "environment")
        self.assertEqual(links.ALLOWED_LINK_HOSTS, ("claude.ai", "www.claude.ai"))

    def test_the_fixture_carries_no_real_capability_material(self) -> None:
        """The token in this file is typed by hand and grants nothing."""
        self.assertIn("FAKE", SAMPLE_TOKEN)
        self.assertEqual(len(SAMPLE_TOKEN), 28)


class ConfirmedFormatTests(unittest.TestCase):
    """The live-confirmed structure, and everything close to it that is refused.

    Every URL here is fabricated. The shape came from two bounded PTY startups
    in M2H PR2.5; no capability material from those runs is in this file, this
    repository, or its history.
    """

    def _link(self, token: str) -> str:
        return "https://claude.ai/code?environment=" + token

    def test_the_confirmed_shape_is_recognised(self) -> None:
        with confirmed_format():
            line = "Continue coding in the Claude mobile app or " + SAMPLE_LINK
            self.assertEqual(links.find_link(line), SAMPLE_LINK)

    def test_the_www_host_is_also_accepted(self) -> None:
        url = "https://www.claude.ai/code?environment=" + SAMPLE_TOKEN
        with confirmed_format():
            self.assertEqual(links.find_link(url), url)

    def test_a_different_path_is_refused(self) -> None:
        """The old pattern accepted any path; a login page is not a session."""
        with confirmed_format():
            for path in ("/login", "/code/session", "/", "/codex", "/settings"):
                with self.subTest(path=path):
                    url = "https://claude.ai" + path + "?environment=" + SAMPLE_TOKEN
                    self.assertIsNone(links.find_link(url))

    def test_a_different_query_key_is_refused(self) -> None:
        with confirmed_format():
            for key in ("env", "environments", "session", "token"):
                with self.subTest(key=key):
                    self.assertIsNone(
                        links.find_link(
                            "https://claude.ai/code?" + key + "=" + SAMPLE_TOKEN
                        )
                    )

    def test_a_link_with_no_query_is_refused(self) -> None:
        with confirmed_format():
            self.assertIsNone(links.find_link("https://claude.ai/code"))
            self.assertIsNone(links.find_link("https://claude.ai/code?environment="))

    def test_a_truncated_token_is_refused(self) -> None:
        """Better no link than a capability URL that fails somewhere else."""
        with confirmed_format():
            for length in (1, 4, 8, links.LINK_TOKEN_MIN_CHARS - 1):
                with self.subTest(length=length):
                    self.assertIsNone(links.find_link(self._link("A" * length)))

    def test_an_over_long_token_is_refused(self) -> None:
        with confirmed_format():
            self.assertIsNone(
                links.find_link(self._link("A" * (links.LINK_TOKEN_MAX_CHARS + 1)))
            )

    def test_a_token_is_never_matched_in_part(self) -> None:
        """The trailing assertion: no prefix of a longer token is a link."""
        with confirmed_format():
            long_token = "B" * (links.LINK_TOKEN_MAX_CHARS + 40)
            self.assertIsNone(links.find_link(self._link(long_token)))

    def test_plain_http_is_refused(self) -> None:
        with confirmed_format():
            self.assertIsNone(
                links.find_link("http://claude.ai/code?environment=" + SAMPLE_TOKEN)
            )

    def test_a_lookalike_host_is_refused(self) -> None:
        with confirmed_format():
            for host in (
                "claude.ai.attacker.test",
                "notclaude.ai",
                "claude.ai.evil.test",
                "evil.test",
            ):
                with self.subTest(host=host):
                    self.assertIsNone(
                        links.find_link(
                            "https://" + host + "/code?environment=" + SAMPLE_TOKEN
                        )
                    )

    def test_a_control_sequence_cannot_forge_an_allowed_host_or_path(self) -> None:
        """The dangerous direction: renders as one thing, matches as another.

        Recognition runs on the *stripped* text, which is what a terminal shows
        a person. So the case that must never pass is output that reads as some
        other host while matching as Claude — a colour reset does not launder
        ``evil.test`` into an allowlisted host, and does not launder ``/login``
        into ``/code``.
        """
        with confirmed_format():
            for forged in (
                "https://evil.test\x1b[0m/code?environment=" + SAMPLE_TOKEN,
                "https://claude.ai.evil.test\x1b[0m/code?environment=" + SAMPLE_TOKEN,
                "https://claude.ai\x1b[0m/login?environment=" + SAMPLE_TOKEN,
            ):
                with self.subTest(forged=forged[:34]):
                    self.assertIsNone(links.find_link(forged))

    def test_decoration_inside_a_genuine_link_is_removed_not_rejected(self) -> None:
        """The benign direction, which the real CLI actually produces.

        Remote Control underlines its URL, so escape sequences land *inside* the
        string. Stripping them yields exactly the link the person sees on screen,
        and refusing it instead would mean failing to capture a link that is
        plainly there.
        """
        with confirmed_format():
            decorated = "https://claude\x1b[0m.ai/code?environment=" + SAMPLE_TOKEN
            self.assertEqual(links.find_link(decorated), SAMPLE_LINK)

    def test_the_real_startup_line_shape_is_recognised(self) -> None:
        """The line the CLI actually prints, reconstructed with a fake token."""
        with confirmed_format():
            painted = (
                "\x1b[2K\r  Continue coding in the Claude mobile app or "
                "\x1b[4m" + SAMPLE_LINK + "\x1b[0m"
            )
            self.assertEqual(links.find_link(painted), SAMPLE_LINK)

    def test_a_repainted_line_does_not_produce_a_glued_token(self) -> None:
        """Carriage-return repaints must not concatenate two links into one."""
        with confirmed_format():
            repainted = SAMPLE_LINK + "\r" + SAMPLE_LINK
            self.assertEqual(links.find_link(repainted.split("\r")[0]), SAMPLE_LINK)

    def test_a_restart_does_not_invalidate_the_link_and_we_do_not_claim_it_does(
        self,
    ) -> None:
        """The live finding M2H PR2 had backwards, pinned so it stays honest.

        The parameter is ``environment``, not ``session``: two generations
        started minutes apart produced the same URL, and the CLI prints
        "Environment preserved" on shutdown. Cofferdam's generation rules stop
        *Cofferdam* handing out a stale link; they do not revoke it upstream.

        This test guards the documentation rather than a branch, because the
        dangerous version of this system is one whose comments promise a
        revocation it cannot perform.
        """
        source = (REPO_ROOT / "cofferdam" / "workstation" / "sessions" / "links.py").read_text(
            "utf-8"
        )
        self.assertIn("Restarting is **not** revocation", source)
        self.assertEqual(links.LINK_QUERY_KEY, "environment")

        # Same environment, two generations: identical URLs are expected, and
        # the store still keeps them apart by generation.
        store = MemoryLinkStore({"demo": {"generation": "g2", "link": SAMPLE_LINK}})
        supervisor = _supervisor(
            FakeRunner(
                default=FakeCompleted(0, show_output(active_state="active", sub_state="running"))
            ),
            make_project("demo"),
            store=store,
        )
        with confirmed_format():
            payload = supervisor.link("demo")
        self.assertEqual(payload["generation"], "g2")

    def test_redaction_removes_the_confirmed_link(self) -> None:
        redacted = links.redact("open " + SAMPLE_LINK + " now")
        self.assertNotIn(SAMPLE_TOKEN, redacted)
        self.assertNotIn("claude.ai", redacted)
        self.assertIn(links.REDACTION, redacted)


class TerminalDecorationTests(unittest.TestCase):
    """Recognition on output that came off a terminal rather than a pipe."""

    def test_a_link_wrapped_in_colour_codes_is_still_found(self) -> None:
        with confirmed_format():
            painted = "\x1b[1m\x1b[36m" + SAMPLE_LINK + "\x1b[0m"
            self.assertEqual(links.find_link(painted), SAMPLE_LINK)

    def test_a_colour_reset_inside_the_url_does_not_forge_a_host(self) -> None:
        """Stripping happens before matching, never mid-match.

        The attack this closes: control characters spliced into a URL so that
        the visible text reads as an allowed host while the bytes do not.
        """
        with confirmed_format():
            spliced = "https://evil.test\x1b[0m/claude.ai/code/session/x"
            self.assertIsNone(links.find_link(spliced))

    def test_an_osc8_hyperlink_target_is_extracted(self) -> None:
        """The URL lives in the escape sequence; the visible text is a label."""
        with confirmed_format():
            hyperlink = "\x1b]8;;" + SAMPLE_LINK + "\x07open session\x1b]8;;\x07"
            self.assertEqual(links.find_link(hyperlink), SAMPLE_LINK)
            self.assertEqual(links.hyperlink_targets(hyperlink), [SAMPLE_LINK])

    def test_an_osc8_hyperlink_is_gated_like_any_other(self) -> None:
        hyperlink = "\x1b]8;;" + SAMPLE_LINK + "\x07open session\x1b]8;;\x07"
        with unconfirmed_format():
            self.assertIsNone(links.find_link(hyperlink))

    def test_an_osc8_target_on_a_foreign_host_is_refused(self) -> None:
        with confirmed_format():
            hyperlink = "\x1b]8;;https://evil.test/code/x\x07claude.ai\x1b]8;;\x07"
            self.assertIsNone(links.find_link(hyperlink))

    def test_stripping_leaves_ordinary_text_alone(self) -> None:
        self.assertEqual(links.strip_ansi("plain output"), "plain output")


class ConsentPromptTests(unittest.TestCase):
    """The M2H PR2 live finding: the CLI stops and asks before it starts.

    Observed on this workstation through a PTY with the fixed argv. The host
    renders its explanation and then waits on ``Enable Remote Control? (y/n)``,
    which ``stdin=/dev/null`` cannot answer — so the unit is active, healthy,
    and will never publish a session.
    """

    def test_the_observed_prompt_is_recognised(self) -> None:
        self.assertTrue(wrapper.CONSENT_FORMAT_CONFIRMED)
        self.assertTrue(wrapper.detect_consent_required("Enable Remote Control? (y/n)"))

    def test_recognition_survives_terminal_decoration(self) -> None:
        painted = "\x1b[2K\r\x1b[1mEnable Remote Control?\x1b[0m (y/n)"
        self.assertTrue(wrapper.detect_consent_required(painted))

    def test_ordinary_output_is_not_a_consent_prompt(self) -> None:
        for line in (
            "Remote Control is enabled",
            "Starting Remote Control...",
            "The session keeps running on this machine.",
            "",
        ):
            with self.subTest(line=line):
                self.assertFalse(wrapper.detect_consent_required(line))

    def test_the_wrapper_reports_the_prompt_once(self) -> None:
        seen = []

        def popen(argv, **kwargs):
            return _FakeProcess(
                ["Enable Remote Control? (y/n)", "Enable Remote Control? (y/n)"],
                0,
                slave=kwargs.get("stdout"),
            )

        host = wrapper.SupervisedHost(
            ["/usr/bin/claude", "remote-control"],
            cwd="/srv/demo",
            on_consent_required=lambda: seen.append(True),
            log=lambda message: None,
            popen=popen,
        )
        host.run()
        self.assertEqual(seen, [True])

    def test_a_prompt_split_across_two_reads_is_still_recognised(self) -> None:
        """It is a prompt: no trailing newline, and a terminal may split it."""
        seen = []
        host = wrapper.SupervisedHost(
            ["/usr/bin/claude"], cwd="/srv/demo",
            on_consent_required=lambda: seen.append(True),
            log=lambda message: None,
        )
        host._absorb_chunk("Enable Remo")
        self.assertEqual(seen, [])
        host._absorb_chunk("te Control? (y/n)")
        self.assertEqual(seen, [True])

    def test_the_marker_buffer_is_bounded(self) -> None:
        host = wrapper.SupervisedHost(
            ["/usr/bin/claude"], cwd="/srv/demo", log=lambda message: None
        )
        for _ in range(50):
            host._absorb_chunk("x" * 200)
        self.assertLessEqual(len(host._marker_tail), wrapper.MARKER_TAIL_CHARS)

    def test_the_prompt_is_not_answered(self) -> None:
        """Consent is the user's to give. stdin stays closed even for this."""
        captured = {}

        def popen(argv, **kwargs):
            captured.update(kwargs)
            return _FakeProcess(["Enable Remote Control? (y/n)"], 0, slave=kwargs.get("stdout"))

        host = wrapper.SupervisedHost(
            ["/usr/bin/claude"], cwd="/srv/demo",
            on_consent_required=lambda: None,
            log=lambda message: None,
            popen=popen,
        )
        host.run()
        self.assertEqual(captured["stdin"], subprocess.DEVNULL)

    def test_no_link_is_invented_from_the_consent_screen(self) -> None:
        """The real screen names claude.ai/code as prose. It is not a session."""
        with confirmed_format():
            prose = (
                "Open the Code tab in the Claude mobile app, or visit "
                "claude.ai/code in a browser."
            )
            self.assertIsNone(links.find_link(prose))


class ChunkedCaptureTests(unittest.TestCase):
    def test_a_link_split_across_chunks_is_found(self) -> None:
        with confirmed_format():
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
        with confirmed_format():
            scanner = links.LinkScanner()
            third = len(SAMPLE_LINK) // 3
            scanner.feed(SAMPLE_LINK[:third])
            scanner.feed(SAMPLE_LINK[third : 2 * third])
            self.assertEqual(scanner.feed(SAMPLE_LINK[2 * third :] + "\n"), SAMPLE_LINK)

    def test_only_the_first_link_is_kept(self) -> None:
        with confirmed_format():
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
    """A child that writes to the PTY slave it was handed, then exits.

    The wrapper now gives the child a pseudo-terminal, so a double that only
    exposes a ``.stdout`` object would be testing an I/O shape production no
    longer uses. This writes real bytes into the real slave descriptor.
    """

    def __init__(self, lines, status=0, slave=None):
        self.pid = os.getpid()
        self._status = status
        self.signals = []
        if slave is not None:
            try:
                for line in lines:
                    os.write(slave, line.encode())
            except OSError:
                pass

    def poll(self):
        return self._status

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
            return _FakeProcess(lines, status, slave=kwargs.get("stdout"))

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
        """The terminal is one-way: output only, no prompt channel."""
        _, captured, _, _ = self._run(["ready\n"])
        self.assertEqual(captured["kwargs"]["stdin"], subprocess.DEVNULL)

    def test_the_child_is_given_a_terminal_not_a_pipe(self) -> None:
        """The reason this PR exists: a pipe produced no session URL at all."""
        _, captured, _, _ = self._run(["ready\n"])
        stdout = captured["kwargs"]["stdout"]
        self.assertIsInstance(stdout, int)
        self.assertEqual(stdout, captured["kwargs"]["stderr"])

    def test_a_platform_without_a_terminal_falls_back_to_a_pipe(self) -> None:
        """Never ``stdout=None``. Inheriting would write raw output to journald."""
        captured = {}

        def popen(argv, **kwargs):
            captured.update(kwargs)
            process = _FakeProcess([], 0, slave=None)
            process.stdout = io.BytesIO(b"ready\n")
            return process

        host = wrapper.SupervisedHost(
            ["/usr/bin/claude"], cwd="/srv/demo", log=lambda message: None, popen=popen
        )
        host._open_terminal = lambda: (None, None)
        host.run()
        self.assertEqual(captured["stdout"], subprocess.PIPE)
        self.assertEqual(captured["stderr"], subprocess.STDOUT)
        self.assertIsNotNone(captured["stdout"])

    def test_retained_output_is_redacted_and_stripped(self) -> None:
        _, _, _, host = self._run(["starting\r\n", "\x1b[32m" + SAMPLE_LINK + "\x1b[0m\r\n"])
        joined = " ".join(host.retained)
        self.assertNotIn(SAMPLE_LINK, joined)
        self.assertNotIn("claude.ai", joined)
        self.assertNotIn("\x1b", joined)

    def test_retained_output_is_bounded(self) -> None:
        _, _, _, host = self._run(["line %d\n" % i for i in range(500)])
        self.assertLessEqual(len(host.retained), wrapper.MAX_RETAINED_LINES)

    def test_a_long_line_is_truncated_in_retention(self) -> None:
        """Retention is capped per line as well as by line count.

        Sized just over the cap rather than pathologically. A pseudo-terminal
        has a finite kernel buffer, and a fixture that writes tens of kilobytes
        faster than the reader drains blocks on its own write — which tests the
        harness, not the wrapper. The production guard against a genuinely
        enormous line is the same cap asserted here.
        """
        _, _, _, host = self._run(["y" * (wrapper.MAX_LINE_CHARS + 500) + "\n"])
        self.assertTrue(host.retained)
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
        self.assertFalse(wrapper.AUTH_FORMAT_CONFIRMED)
        for line in ("please log in", "you must authenticate", "subscription required"):
            with self.subTest(line=line):
                self.assertFalse(wrapper.detect_auth_required(line))

    def test_no_link_is_captured_while_the_format_is_unconfirmed(self) -> None:
        """The gate, end to end through the real wrapper."""
        with unconfirmed_format():
            _, _, recorded, _ = self._run(["starting\n", SAMPLE_LINK + "\n"])
        self.assertEqual(recorded, [])


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

    def test_a_requested_stop_reports_success_not_the_kill_status(self) -> None:
        """The M2H PR2 live-spike regression, pinned.

        A deliberate `systemctl stop` used to leave the unit `failed` with exit
        137: the signal handler called `wait()` re-entrantly on a child the main
        thread was already waiting on, which returned at once and escalated
        straight to SIGKILL, and the kill status was then propagated as the
        unit's exit code. With Restart=on-failure that is a lie that restarts
        things nobody asked to restart.
        """
        import threading
        import time

        host = wrapper.SupervisedHost(
            ["/bin/sh", "-c", "sleep 60"], cwd="/tmp", log=lambda message: None
        )
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("code", host.run()))
        thread.start()
        for _ in range(100):
            if host._process is not None:
                break
            time.sleep(0.02)
        host.terminate()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result.get("code"), 0, "a requested stop is not a failure")

    def test_terminate_does_not_block_the_caller(self) -> None:
        """It runs in a signal handler; it must return promptly."""
        import threading
        import time

        host = wrapper.SupervisedHost(
            ["/bin/sh", "-c", "trap '' TERM; sleep 30"], cwd="/tmp", log=lambda m: None
        )
        thread = threading.Thread(target=host.run)
        thread.start()
        for _ in range(100):
            if host._process is not None:
                break
            time.sleep(0.02)
        started = time.monotonic()
        host.terminate()
        self.assertLess(time.monotonic() - started, 2.0, "terminate() blocked")
        host._escalation.cancel()
        host._process.kill()
        thread.join(timeout=15)

    def test_a_real_child_link_is_captured_and_redacted(self) -> None:
        recorded = []
        retained = []
        host = wrapper.SupervisedHost(
            ["/bin/sh", "-c", "echo starting; echo " + SAMPLE_LINK + "; echo done"],
            cwd="/tmp",
            on_link=recorded.append,
            log=retained.append,
        )
        with confirmed_format():
            code = host.run()
        self.assertEqual(code, 0)
        self.assertEqual(recorded, [SAMPLE_LINK], "captured through a real PTY")
        self.assertNotIn(SAMPLE_LINK, " ".join(host.retained))
        self.assertNotIn(SAMPLE_LINK, " ".join(retained))

    def test_a_real_child_link_is_not_captured_while_unconfirmed(self) -> None:
        """Same child, gate closed: nothing is captured and nothing is stored."""
        recorded = []
        host = wrapper.SupervisedHost(
            ["/bin/sh", "-c", "echo " + SAMPLE_LINK],
            cwd="/tmp",
            on_link=recorded.append,
            log=lambda message: None,
        )
        with unconfirmed_format():
            self.assertEqual(host.run(), 0)
        self.assertEqual(recorded, [])


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


class StopTimeoutTests(unittest.TestCase):
    """A stop that works must not be reported as a backend failure.

    Found by the M2H PR2 live validation: ``systemctl stop`` blocked for about
    fifteen seconds because the CLI ignores SIGTERM, hit the shared control
    timeout, and the route answered 503 for a unit that had in fact stopped
    cleanly with status 0.
    """

    def test_the_stop_bound_exceeds_the_units_own_shutdown_bound(self) -> None:
        template = (REPO_ROOT / "deploy" / systemd.__name__.split(".")[-1]).parent
        unit = (REPO_ROOT / "deploy" / "cofferdam-rc@.service").read_text()
        declared = int(re.search(r"TimeoutStopSec=(\d+)", unit).group(1))
        self.assertGreater(
            systemd.STOP_TIMEOUT_SECONDS,
            declared,
            "a stop can take the whole of the unit's TimeoutStopSec",
        )
        self.assertGreater(systemd.STOP_TIMEOUT_SECONDS, systemd.CONTROL_TIMEOUT_SECONDS)
        self.assertTrue(template.exists())

    def test_stop_is_given_the_longer_timeout_and_start_is_not(self) -> None:
        seen = []

        def runner(argv, timeout=None):
            seen.append((argv[2], timeout))
            return FakeCompleted(0, "")

        backend = SystemdUserBackend(runner)
        backend.start("demo")
        backend.stop("demo")
        self.assertEqual(seen[0][0], "start")
        self.assertEqual(seen[0][1], systemd.CONTROL_TIMEOUT_SECONDS)
        self.assertEqual(seen[1][0], "stop")
        self.assertEqual(seen[1][1], systemd.STOP_TIMEOUT_SECONDS)


class StartSettleTests(unittest.TestCase):
    """The generation must be known by the time a start returns.

    Also found live: ``systemctl start`` returns on fork, the host writes its
    generation a moment later, and a status read in between reports ``None`` —
    which made "did the second launch differ from the first" compare two blanks
    and pass for the wrong reason.
    """

    class _Backend:
        """Reports ``inactive`` until asked to start, then ``active``.

        The real launch shape, which is what makes the race reachable: the unit
        must genuinely go from down to up for the supervisor to take the start
        path rather than the idempotent one.
        """

        def __init__(self, states=None) -> None:
            self.started = 0
            self._states = list(states or [])

        def status(self, project_id):
            if self._states:
                active = self._states.pop(0)
            else:
                active = "active" if self.started else "inactive"
            return NativeSessionStatus(
                project_id=project_id,
                unit="cofferdam-rc@" + project_id + ".service",
                state=map_active_state(active),
                active_state=active,
            )

        def start(self, project_id):
            self.started += 1

    def _supervisor(self, backend, store, sleep):
        return RemoteControlSupervisor(
            provider(make_project("demo", remote_control_enabled=True)),
            backend=backend,
            store=store,
            clock=fixed_clock(),
            sleep=sleep,
        )

    def test_start_waits_for_the_host_to_write_its_generation(self) -> None:
        store = MemoryLinkStore()
        slept = []

        def late_writer(seconds):
            slept.append(seconds)
            store.documents["demo"] = {"generation": "gen-1"}

        status = self._supervisor(self._Backend(), store, late_writer).start("demo")
        self.assertEqual(status.generation, "gen-1")
        self.assertTrue(slept, "it should have waited at least once")

    def test_a_host_that_never_identifies_itself_is_not_given_a_generation(self) -> None:
        """No invention. A blank is honest; a made-up generation is not."""
        supervisor = self._supervisor(self._Backend(), MemoryLinkStore(), lambda _s: None)
        self.assertIsNone(supervisor.start("demo").generation)

    def test_the_wait_is_bounded(self) -> None:
        slept = []
        supervisor = self._supervisor(
            self._Backend(), MemoryLinkStore(), lambda seconds: slept.append(seconds)
        )
        supervisor.start("demo")
        self.assertLessEqual(len(slept), supervisor_module.START_SETTLE_ATTEMPTS)
        self.assertLessEqual(
            sum(slept),
            supervisor_module.START_SETTLE_ATTEMPTS * supervisor_module.START_SETTLE_SECONDS,
        )

    def test_an_already_running_host_reports_its_existing_generation(self) -> None:
        """Idempotent repeat start: the same generation, not a blank."""
        backend = self._Backend()
        backend.started = 1
        store = MemoryLinkStore({"demo": {"generation": "gen-1"}})
        supervisor = self._supervisor(backend, store, lambda _s: None)
        self.assertEqual(supervisor.start("demo").generation, "gen-1")
        self.assertEqual(backend.started, 1, "no second host for a double tap")

    def test_a_unit_that_never_comes_up_is_not_waited_on(self) -> None:
        """Nothing to settle if there is no live host to settle into."""
        slept = []
        backend = self._Backend(states=["inactive", "inactive", "inactive"])
        supervisor = self._supervisor(
            backend, MemoryLinkStore(), lambda seconds: slept.append(seconds)
        )
        supervisor.start("demo")
        self.assertEqual(slept, [])


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

    def test_a_host_waiting_for_consent_says_so_instead_of_just_running(self) -> None:
        """The gap this PR found: ``active`` and useless at the same time.

        systemd is not wrong — the process is up. But nothing is reachable from
        a phone and nothing will be until somebody answers the prompt at the
        machine, so the status carries that fact rather than leaving a reader to
        infer health from ``running``.
        """
        store = MemoryLinkStore({"demo": {"generation": "g", "awaiting_consent": True}})
        status = _supervisor(self._running(), make_project("demo"), store=store).status("demo")
        self.assertEqual(status.state, STATE_RUNNING)
        self.assertTrue(status.awaiting_consent)
        self.assertFalse(status.url_available)
        self.assertTrue(status.to_dict()["awaiting_consent"])

    def test_consent_is_false_unless_the_prompt_was_actually_seen(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g"}})
        status = _supervisor(self._running(), make_project("demo"), store=store).status("demo")
        self.assertFalse(status.awaiting_consent)

    def test_consent_evidence_is_ignored_when_the_unit_is_not_live(self) -> None:
        store = MemoryLinkStore({"demo": {"generation": "g", "awaiting_consent": True}})
        runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="inactive")))
        status = _supervisor(runner, make_project("demo"), store=store).status("demo")
        self.assertEqual(status.state, STATE_STOPPED)
        self.assertFalse(status.awaiting_consent)

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
        with confirmed_format():
            payload = _supervisor(self._running(), make_project("demo"), store=store).link(
                "demo"
            )
        self.assertEqual(payload["url"], SAMPLE_LINK)
        self.assertEqual(payload["generation"], "g1")

    def test_retrieval_refuses_truthfully_while_the_format_is_unconfirmed(self) -> None:
        """Even with a stored link and a live unit, the gate refuses.

        Second lock on the same door: `find_link` cannot recognise anything, so
        nothing should be stored — but this asserts the retrieval boundary
        refuses on its own terms rather than relying on that.
        """
        store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
        supervisor = _supervisor(self._running(), make_project("demo"), store=store)
        with unconfirmed_format():
            with self.assertRaises(LinkUnavailable) as caught:
                supervisor.link("demo")
        self.assertIn("not been confirmed", caught.exception.detail)

    def test_retrieval_works_once_the_format_is_confirmed(self) -> None:
        store = MemoryLinkStore(
            {"demo": {"generation": "g1", "link": SAMPLE_LINK, "discovered_at": "T"}}
        )
        with confirmed_format():
            payload = _supervisor(self._running(), make_project("demo"), store=store).link(
                "demo"
            )
        self.assertEqual(payload["url"], SAMPLE_LINK)

    def test_no_link_before_capture(self) -> None:
        supervisor = _supervisor(self._running(), make_project("demo"))
        with self.assertRaises(LinkUnavailable):
            supervisor.link("demo")

    def test_no_link_when_the_host_is_stopped(self) -> None:
        with confirmed_format():
            store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
            runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="inactive")))
            with self.assertRaises(LinkUnavailable):
                _supervisor(runner, make_project("demo"), store=store).link("demo")

    def test_no_link_when_the_host_failed(self) -> None:
        with confirmed_format():
            store = MemoryLinkStore({"demo": {"generation": "g", "link": SAMPLE_LINK}})
            runner = FakeRunner(default=FakeCompleted(0, show_output(active_state="failed")))
            with self.assertRaises(LinkUnavailable):
                _supervisor(runner, make_project("demo"), store=store).link("demo")

    def test_a_link_cannot_be_read_across_projects(self) -> None:
        with confirmed_format():
            store = MemoryLinkStore({"alpha": {"generation": "g", "link": SAMPLE_LINK}})
            supervisor = _supervisor(
                self._running(), make_project("alpha"), make_project("beta"), store=store
            )
            self.assertEqual(supervisor.link("alpha")["url"], SAMPLE_LINK)
            with self.assertRaises(LinkUnavailable):
                supervisor.link("beta")

    def test_an_unknown_project_cannot_retrieve_a_link(self) -> None:
        with confirmed_format():
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
