"""Behavioural + terminal-safety tests for ``cofferdam approve`` (PR3c1).

Drives the full interactive command with faked TTY streams: proposal input,
guard/binding, the exact-display safety screen, the TTY gate, one-attempt
confirmation, the post-confirmation recompute-under-lock, and exit codes.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from types import SimpleNamespace

from cofferdam import cli, hashing
from cofferdam.approval import _find_valid_approval
from cofferdam.approval_store import _ApprovalStore
from cofferdam.approve_cli import (
    _bad_char_class,
    _escape_field,
    _escape_line,
    _read_confirmation,
    _render_display,
    _screen_renderable,
)
from cofferdam.clock import SystemClock
from cofferdam.dryrun import build_dry_run_artifact
from cofferdam.proposal import parse_proposal
from cofferdam.repo_view import FilesystemRepoView

from tests._approval_doubles import FakeClock, make_approval_entry, seed_approval

_DIFF = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
_PROPOSAL = {
    "schema_version": 1,
    "kind": "single_file_diff",
    "target_path": "src/app.py",
    "diff": _DIFF,
}


class _Stream(io.StringIO):
    """A StringIO that reports as a TTY (or not), can raise on / hook into
    ``readline`` to model EOF, interrupts, and TOCTOU races, can fail on selected
    ``write`` calls to model a terminal that cannot encode a character
    (``UnicodeEncodeError``) or a broken stream, and can fail on selected
    ``flush`` calls to model a fully buffered stream whose delivery fails. An
    optional shared ``events`` log records the relative order of flush vs
    readline so tests can prove confirmation is never read before a good flush."""

    def __init__(self, initial="", tty=True, readline_exc=None, on_readline=None,
                 fail_write_indices=None, flush_fail_indices=None, flush_exc=None,
                 events=None, name=None):
        super().__init__(initial)
        self._tty = tty
        self._exc = readline_exc
        self._cb = on_readline
        self._fail_writes = set(fail_write_indices or ())
        self._write_count = 0
        self._flush_fail = set(flush_fail_indices or ())
        self._flush_exc = flush_exc or OSError("simulated flush failure")
        self._flush_count = 0
        self._events = events
        self._name = name
        self.readline_count = 0

    def isatty(self):
        return self._tty

    def readline(self, size=-1):
        self.readline_count += 1
        if self._events is not None:
            self._events.append(("readline", self._name))
        if self._cb is not None:
            self._cb()
        if self._exc is not None:
            raise self._exc
        return super().readline(size)

    def write(self, s):
        self._write_count += 1
        if self._write_count in self._fail_writes:
            raise UnicodeEncodeError("utf-8", s, 0, 1, "simulated encode failure")
        return super().write(s)

    def flush(self):
        self._flush_count += 1
        if self._events is not None:
            self._events.append(("flush", self._name))
        if self._flush_count in self._flush_fail:
            raise self._flush_exc
        return super().flush()


class ApproveCliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("old\n")
        self.proposal_path = self.root / "proposal.json"
        self.proposal_path.write_text(json.dumps(_PROPOSAL))
        self.addCleanup(self._tmp.cleanup)
        self.view = FilesystemRepoView(self.root)
        self.bh = build_dry_run_artifact(parse_proposal(_PROPOSAL).proposal, self.view).bound_hash

    def _phrase(self):
        return "APPROVE " + self.bh[:12]

    def _run(self, argv, stdin_text="", stdin_tty=True, stdout_tty=True,
             stderr_tty=True, stdin_exc=None, on_readline=None,
             stdout_fail_indices=None, stderr_fail_indices=None,
             stdout_flush_fail_indices=None, stderr_flush_fail_indices=None,
             stdout_flush_exc=None, events=None):
        stdin = _Stream(stdin_text, tty=stdin_tty, readline_exc=stdin_exc,
                        on_readline=on_readline, events=events, name="stdin")
        stdout = _Stream(tty=stdout_tty, fail_write_indices=stdout_fail_indices,
                         flush_fail_indices=stdout_flush_fail_indices,
                         flush_exc=stdout_flush_exc, events=events, name="stdout")
        stderr = _Stream(tty=stderr_tty, fail_write_indices=stderr_fail_indices,
                         flush_fail_indices=stderr_flush_fail_indices)
        # Expose the streams so tests can assert on readline_count / ordering.
        self.last_stdin, self.last_stdout, self.last_stderr = stdin, stdout, stderr
        with mock.patch.object(cli.sys, "stdin", stdin), \
             mock.patch("cofferdam.approve_cli.sys.stdin", stdin), \
             mock.patch("cofferdam.approve_cli.sys.stdout", stdout), \
             mock.patch("cofferdam.approve_cli.sys.stderr", stderr), \
             mock.patch("cofferdam.cli.sys.stdout", stdout), \
             mock.patch("cofferdam.cli.sys.stderr", stderr), \
             mock.patch("cofferdam.approve_cli.os.getcwd", return_value=str(self.root)):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def _active(self):
        return _find_valid_approval(
            self.bh, store=_ApprovalStore(self.view), repo_view=self.view, clock=SystemClock()
        )

    def _approve_argv(self):
        return ["approve", "--file", str(self.proposal_path)]


class SuccessTests(ApproveCliBase):
    def test_valid_approval_success(self):
        code, out, err = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn("Approval recorded", out)
        self.assertIn("No file was modified", out)
        self.assertIn(self.bh, out)  # full 64-hex binding shown
        self.assertIsNotNone(self._active())
        # The target file was not touched.
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")

    def test_success_output_has_no_full_approval_id(self):
        self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        with _ApprovalStore(self.view).lock(create=False):
            entries = _ApprovalStore(self.view).read_entries()
        approval_id = entries[0]["approval_id"]
        code, out, err = self._run(  # a second run declines (already active)
            self._approve_argv(), stdin_text="no\n"
        )
        self.assertNotIn(approval_id, out + err)

    def test_complete_patch_and_binding_shown(self):
        code, out, err = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertIn("BEGIN ESCAPED PATCH", out)
        self.assertIn("END ESCAPED PATCH", out)
        self.assertIn("+new", out)
        self.assertIn("-old", out)
        self.assertIn(self.bh, out)
        self.assertIn("ends with LF: yes", out)  # _DIFF ends with a newline

    def test_proposal_file_opened_exactly_once(self):
        real_open = open
        seen = []

        def counting_open(file, *a, **k):
            if not isinstance(file, int):
                seen.append(os.fspath(file))
            return real_open(file, *a, **k)

        with mock.patch("builtins.open", counting_open):
            code, out, err = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 0, err)
        proposal_opens = [p for p in seen if p == str(self.proposal_path)]
        self.assertEqual(len(proposal_opens), 1)

    def test_proposal_file_outside_repo_accepted(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        ext = Path(outside.name) / "p.json"
        ext.write_text(json.dumps(_PROPOSAL))
        code, out, err = self._run(
            ["approve", "--file", str(ext)], stdin_text=self._phrase() + "\n"
        )
        self.assertEqual(code, 0, err)
        self.assertIsNotNone(self._active())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlinked_proposal_accepted(self):
        link = self.root / "link.json"
        try:
            link.symlink_to(self.proposal_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted on this platform")
        code, out, err = self._run(
            ["approve", "--file", str(link)], stdin_text=self._phrase() + "\n"
        )
        self.assertEqual(code, 0, err)


class TabDisplayTests(ApproveCliBase):
    def test_tab_rendered_as_backslash_t(self):
        diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,2 @@\n old\n+\tindented\n"
        proposal = {**_PROPOSAL, "diff": diff}
        self.proposal_path.write_text(json.dumps(proposal))
        bh = build_dry_run_artifact(parse_proposal(proposal).proposal, self.view).bound_hash
        code, out, err = self._run(
            self._approve_argv(), stdin_text="APPROVE " + bh[:12] + "\n"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("+\\tindented", out)   # TAB shown as an escape, not an arrow
        self.assertNotIn("→", out)           # no arrow marker anywhere
        self.assertIn("indented", out)


class InputRejectionTests(ApproveCliBase):
    def test_missing_file(self):
        code, out, err = self._run(["approve", "--file", str(self.root / "nope.json")],
                                   stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_directory_rejected(self):
        code, _, _ = self._run(["approve", "--file", str(self.root / "src")],
                               stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_oversized_file_rejected(self):
        big = self.root / "big.json"
        big.write_bytes(b" " * (2 * 1024 * 1024 + 1))
        code, _, _ = self._run(["approve", "--file", str(big)], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_invalid_utf8_rejected(self):
        bad = self.root / "bad.json"
        bad.write_bytes(b"\xff\xfe{")
        code, _, _ = self._run(["approve", "--file", str(bad)], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_malformed_json_rejected(self):
        bad = self.root / "m.json"
        bad.write_text("{not json")
        code, _, _ = self._run(["approve", "--file", str(bad)], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_invalid_schema_rejected(self):
        bad = self.root / "s.json"
        bad.write_text(json.dumps({"schema_version": 1}))
        code, _, _ = self._run(["approve", "--file", str(bad)], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_caller_supplied_bound_hash_rejected(self):
        bad = self.root / "b.json"
        bad.write_text(json.dumps({**_PROPOSAL, "bound_hash": "a" * 64}))
        code, _, _ = self._run(["approve", "--file", str(bad)], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_stdin_dash_rejected(self):
        code, _, _ = self._run(["approve", "--file", "-"], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_no_file_argument(self):
        code, _, _ = self._run(["approve"], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)

    def test_unknown_argument_rejected(self):
        for extra in ("--yes", "--force", "--non-interactive", "--repo", "--execute", "--json"):
            code, _, _ = self._run(
                ["approve", "--file", str(self.proposal_path), extra],
                stdin_text=self._phrase() + "\n",
            )
            self.assertEqual(code, 2, extra)

    def test_blocked_proposal_rejected(self):
        blocked = {
            "schema_version": 1, "kind": "single_file_diff", "target_path": "setup.py",
            "diff": "--- a/setup.py\n+++ b/setup.py\n@@ -1,1 +1,1 @@\n-a\n+b\n",
        }
        bad = self.root / "blk.json"
        bad.write_text(json.dumps(blocked))
        code, _, _ = self._run(["approve", "--file", str(bad)], stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)
        self.assertFalse((self.root / ".cofferdam").exists())


class DisplaySafetyScreenTests(unittest.TestCase):
    def test_ordinary_text_and_combining_marks_permitted(self):
        self.assertTrue(_screen_renderable("hello world\n\ttabbed"))
        self.assertTrue(_screen_renderable("é"))          # combining acute accent
        self.assertTrue(_screen_renderable("café naïve"))  # precomposed + combining ok

    def test_rejected_classes(self):
        cases = {
            "CR": "a\rb",
            "ESC/ANSI": "a\x1b[31mred",
            "C0": "a\x07b",
            "DEL": "a\x7fb",
            "C1": "a\x85b",
            "bidi": "a‮b",
            "zero-width": "a​b",
            "noncharacter FFFE": "a￾b",
            "noncharacter FDD0": "a﷐b",
            "plane noncharacter": "a\U0001fffeb",
        }
        for name, text in cases.items():
            self.assertFalse(_screen_renderable(text), name)
            self.assertIsNotNone(_bad_char_class(text), name)

    def test_surrogate_rejected(self):
        self.assertFalse(_screen_renderable("a\ud800b"))

    def test_escape_is_display_only(self):
        marked = _escape_line("a\tb")
        self.assertEqual(marked, "a\\tb")   # actual TAB -> the two chars backslash,t
        self.assertNotIn("\t", marked)      # no real TAB survives into the display


class InjectiveDisplayTests(unittest.TestCase):
    """The escaped display must map distinct patch bytes to distinct output, and
    a reader must be able to recover the exact bound bytes from what is shown."""

    def test_actual_tab_vs_literal_arrow(self):
        self.assertNotEqual(_escape_line("\t"), _escape_line("→"))
        self.assertEqual(_escape_line("\t"), "\\t")
        self.assertEqual(_escape_line("→"), "\\u{2192}")

    def test_actual_tab_vs_literal_backslash_t(self):
        # source text backslash+t (two chars) must differ from an actual TAB.
        self.assertNotEqual(_escape_line("\\t"), _escape_line("\t"))
        self.assertEqual(_escape_line("\\t"), "\\\\t")   # backslash doubled

    def test_one_tab_vs_spaces(self):
        self.assertNotEqual(_escape_line("\t"), _escape_line("        "))

    def test_consecutive_tabs_are_countable(self):
        self.assertEqual(_escape_line("\t\t\t"), "\\t\\t\\t")
        self.assertNotEqual(_escape_line("\t\t"), _escape_line("\t"))

    def test_trailing_space_vs_none(self):
        self.assertEqual(_escape_line("a"), "a")
        self.assertEqual(_escape_line("a "), "a\\x20")
        self.assertNotEqual(_escape_line("a "), _escape_line("a"))

    def test_one_vs_multiple_trailing_spaces(self):
        self.assertEqual(_escape_line("a  "), "a\\x20\\x20")
        self.assertNotEqual(_escape_line("a "), _escape_line("a  "))

    def test_literal_backslash_x20_vs_trailing_space(self):
        # source literal backslash,x,2,0 vs an actual trailing space.
        self.assertNotEqual(_escape_line("a\\x20"), _escape_line("a "))
        self.assertEqual(_escape_line("a\\x20"), "a\\\\x20")  # backslash doubled
        self.assertEqual(_escape_line("a "), "a\\x20")

    def test_interior_space_stays_literal_only_trailing_escaped(self):
        self.assertEqual(_escape_line("a b "), "a b\\x20")

    def test_non_ascii_composed_vs_decomposed(self):
        composed = "é"          # é  (single code point U+00E9)
        decomposed = "é"       # e + combining acute (U+0301)
        self.assertNotEqual(_escape_line(composed), _escape_line(decomposed))
        self.assertEqual(_escape_line(composed), "\\u{e9}")
        self.assertEqual(_escape_line(decomposed), "e\\u{301}")

    def test_ordinary_patch_line_stays_readable(self):
        self.assertEqual(_escape_line("+new code here"), "+new code here")
        self.assertEqual(_escape_field("src/app.py"), "src/app.py")

    def _art(self, **kw):
        base = dict(relative_path="src/app.py", guard_risk="low",
                    repo_root_id="a" * 64, bound_hash="b" * 64)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_final_lf_state_is_explicit_and_distinguishing(self):
        with_lf = _render_display(self._art(), SimpleNamespace(diff="a\nb\n"), "x")
        without_lf = _render_display(self._art(), SimpleNamespace(diff="a\nb"), "x")
        self.assertIn("ends with LF: yes", with_lf)
        self.assertIn("ends with LF: no", without_lf)
        self.assertNotEqual(with_lf, without_lf)

    def test_rendered_display_is_pure_ascii(self):
        # An ASCII-only display cannot be partially mangled by a terminal that
        # can't encode arbitrary Unicode (the reason for escaping non-ASCII).
        art = self._art(relative_path="dir/café.py")
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+é → and\ttab trailing  \n"
        out = _render_display(art, SimpleNamespace(diff=diff), "target exists (1 bytes)")
        out.encode("ascii")  # raises if any non-ASCII survived into the display

    def test_bound_hash_uses_original_bytes_not_rendered_text(self):
        # A diff containing a real TAB and one containing the literal text "\t"
        # are different bytes and must bind to different hashes; the display never
        # feeds the binding.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("old\n")
        view = FilesystemRepoView(root)
        tab_diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,2 @@\n old\n+\tx\n"
        txt_diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,2 @@\n old\n+\\tx\n"
        a = build_dry_run_artifact(parse_proposal({**_PROPOSAL, "diff": tab_diff}).proposal, view)
        b = build_dry_run_artifact(parse_proposal({**_PROPOSAL, "diff": txt_diff}).proposal, view)
        self.assertNotEqual(a.bound_hash, b.bound_hash)


class ConfirmationLimitTests(unittest.TestCase):
    """The confirmation limit is 256 UTF-8 BYTES, not Python characters."""

    def _read(self, stdin_text, expected):
        stdin = _Stream(stdin_text)
        with mock.patch("cofferdam.approve_cli.sys.stdin", stdin):
            return _read_confirmation(expected)

    def test_exact_ascii_phrase_accepted(self):
        self.assertTrue(self._read("APPROVE abc\n", "APPROVE abc"))

    def test_256_ascii_bytes_within_limit(self):
        s = "A" * 256
        self.assertTrue(self._read(s + "\n", s))   # exactly 256 bytes accepted

    def test_over_256_ascii_bytes_rejected(self):
        s = "A" * 257
        self.assertFalse(self._read(s + "\n", s))  # 257 bytes rejected even if equal

    def test_multibyte_under_256_chars_but_over_256_bytes_rejected(self):
        s = "€" * 100          # 100 chars, 300 UTF-8 bytes
        self.assertFalse(self._read(s + "\n", s))

    def test_multibyte_within_256_bytes_accepted(self):
        s = "€" * 80           # 80 chars, 240 UTF-8 bytes
        self.assertTrue(self._read(s + "\n", s))

    def test_no_whitespace_stripped_into_acceptance(self):
        self.assertFalse(self._read(" APPROVE abc\n", "APPROVE abc"))
        self.assertFalse(self._read("APPROVE abc \n", "APPROVE abc"))


class TerminalWriteFailureTests(ApproveCliBase):
    """A terminal that cannot render the display/prompt must abort BEFORE any
    mint, with no traceback; a failure of the post-mint success message uses the
    indeterminate-authority posture because the approval already exists."""

    def test_display_write_failure_aborts_before_mint(self):
        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n",
            stdout_fail_indices={1},   # the header/patch display write fails
        )
        self.assertEqual(code, 2)
        self.assertIsNone(self._active())
        self.assertFalse((self.root / ".cofferdam").exists())
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")

    def test_prompt_write_failure_aborts_before_mint(self):
        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n",
            stdout_fail_indices={2},   # display ok, prompt write fails
        )
        self.assertEqual(code, 2)
        self.assertIsNone(self._active())

    def test_stderr_write_failure_no_traceback(self):
        # Force an error path (unknown flag => _err) while stderr write fails;
        # the command must still return an exit code, not raise.
        code, out, err = self._run(
            ["approve", "--file", str(self.proposal_path), "--bogus"],
            stdin_text=self._phrase() + "\n", stderr_fail_indices={1},
        )
        self.assertEqual(code, 2)

    def test_success_write_failure_is_indeterminate_but_approval_exists(self):
        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n",
            stdout_fail_indices={3},   # display + prompt ok, success write fails
        )
        self.assertEqual(code, 2)
        self.assertIsNotNone(self._active())        # the approval WAS recorded
        self.assertIn("approval", err.lower())
        self.assertIn("approval-status", err)       # points to the verify command
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")


class FsyncIndeterminateTests(ApproveCliBase):
    def test_fsync_failure_reports_indeterminate_not_failure(self):
        # Pre-create .cofferdam/ so the ONLY fsync during the mint is the ledger
        # append's; then make that fsync fail after the record is fully written.
        with _ApprovalStore(self.view).lock(create=True):
            pass
        with mock.patch("cofferdam.approval_store.os.fsync", side_effect=OSError("fsync")):
            code, out, err = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)
        self.assertIn("indeterminate", err)
        self.assertNotIn("was not written", err)     # must not claim no approval
        self.assertIn("approval-status", err)        # verify guidance
        # The record was completely written before the failed fsync: discoverable.
        self.assertIsNotNone(self._active())
        # No target mutation, no full approval_id leaked.
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")


class HostileContentTests(ApproveCliBase):
    def test_bidi_diff_is_unapprovable(self):
        diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+ne‮w\n"
        self.proposal_path.write_text(json.dumps({**_PROPOSAL, "diff": diff}))
        code, out, err = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 2)
        self.assertIn("cannot be displayed safely", err)
        self.assertFalse((self.root / ".cofferdam").exists())


class TtyGateTests(ApproveCliBase):
    def test_stdin_not_tty(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n", stdin_tty=False)
        self.assertEqual(code, 2)
        self.assertFalse((self.root / ".cofferdam").exists())

    def test_stdout_not_tty(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n", stdout_tty=False)
        self.assertEqual(code, 2)

    def test_stderr_not_tty(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n", stderr_tty=False)
        self.assertEqual(code, 2)


class ConfirmationTests(ApproveCliBase):
    def test_exact_phrase_accepted(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 0)

    def test_one_char_mismatch(self):
        bad = self._phrase()[:-1] + ("0" if self._phrase()[-1] != "0" else "1")
        code, _, _ = self._run(self._approve_argv(), stdin_text=bad + "\n")
        self.assertEqual(code, 1)
        self.assertIsNone(self._active())

    def test_case_mismatch(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text="approve " + self.bh[:12] + "\n")
        self.assertEqual(code, 1)

    def test_wrong_prefix(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text="APPROVE " + ("0" * 12) + "\n")
        self.assertEqual(code, 1)

    def test_leading_whitespace(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=" " + self._phrase() + "\n")
        self.assertEqual(code, 1)

    def test_trailing_whitespace(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=self._phrase() + " \n")
        self.assertEqual(code, 1)

    def test_excessive_input(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text=("A" * 300) + "\n")
        self.assertEqual(code, 1)

    def test_embedded_control_rejected(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text="APPROVE \x07" + self.bh[:12] + "\n")
        self.assertEqual(code, 1)

    def test_eof(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text="")
        self.assertEqual(code, 1)
        self.assertFalse((self.root / ".cofferdam").exists())

    def test_keyboard_interrupt(self):
        code, _, _ = self._run(self._approve_argv(), stdin_exc=KeyboardInterrupt())
        self.assertEqual(code, 1)

    def test_no_state_created_on_decline(self):
        code, _, _ = self._run(self._approve_argv(), stdin_text="nope\n")
        self.assertEqual(code, 1)
        self.assertFalse((self.root / ".cofferdam").exists())


class PostConfirmationTests(ApproveCliBase):
    def test_target_change_during_confirmation_refuses(self):
        def mutate():
            (self.root / "src" / "app.py").write_text("CHANGED\n")

        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n", on_readline=mutate
        )
        self.assertEqual(code, 2)
        self.assertIn("changed during confirmation", err)
        self.assertIsNone(self._active())

    def test_proposal_file_change_during_confirmation_ignored(self):
        # Rewriting the proposal *file* mid-flow must not affect the mint — the
        # already-parsed immutable Proposal is authoritative and is not re-read.
        def rewrite():
            self.proposal_path.write_text(json.dumps({**_PROPOSAL, "target_path": "other.py"}))

        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n", on_readline=rewrite
        )
        self.assertEqual(code, 0, err)
        self.assertIsNotNone(self._active())


class DuplicateTests(ApproveCliBase):
    def test_active_duplicate_exits_1(self):
        root_id = hashing.repo_root_id(self.view.root_bytes())
        import time
        seed_approval(
            _ApprovalStore(self.view),
            make_approval_entry(
                bound_hash=self.bh, repo_root_id=root_id,
                created_at=int(time.time()), ttl=3600,
            ),
        )
        code, out, err = self._run(self._approve_argv(), stdin_text=self._phrase() + "\n")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)


class FlushGateTests(ApproveCliBase):
    """No confirmation may be read and no approval minted unless the complete
    display + prompt were successfully written AND flushed to the terminal."""

    def test_prompt_flush_failure_aborts_before_confirmation(self):
        # Writes succeed, but the checked flush before confirmation fails.
        with mock.patch("cofferdam.approve_cli._mint",
                        side_effect=AssertionError("_mint must not be called")) as m:
            code, out, err = self._run(
                self._approve_argv(), stdin_text=self._phrase() + "\n",
                stdout_flush_fail_indices={1},   # first (pre-confirmation) flush fails
            )
        self.assertEqual(code, 2)
        m.assert_not_called()
        self.assertEqual(self.last_stdin.readline_count, 0)  # confirmation never read
        self.assertIsNone(self._active())
        self.assertFalse((self.root / ".cofferdam").exists())
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")

    def test_confirmation_read_only_after_successful_flush(self):
        # Ordering proof on an event-logging stream: a good stdout flush must
        # precede the stdin readline.
        events = []
        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n", events=events
        )
        self.assertEqual(code, 0, err)
        self.assertIn(("flush", "stdout"), events)
        self.assertIn(("readline", "stdin"), events)
        # The last flush before the first readline is a stdout flush.
        first_read = events.index(("readline", "stdin"))
        flushes_before = [i for i, e in enumerate(events[:first_read]) if e == ("flush", "stdout")]
        self.assertTrue(flushes_before, "no stdout flush occurred before confirmation was read")

    def test_buffered_stream_failed_flush_prevents_confirmation(self):
        # A fully buffered TTY-like stream whose flush fails must not reach input.
        events = []
        with mock.patch("cofferdam.approve_cli._mint",
                        side_effect=AssertionError("_mint must not be called")):
            code, out, err = self._run(
                self._approve_argv(), stdin_text=self._phrase() + "\n",
                stdout_flush_fail_indices={1}, events=events,
            )
        self.assertEqual(code, 2)
        self.assertNotIn(("readline", "stdin"), events)

    def test_flush_failure_after_mint_is_indeterminate(self):
        # First (pre-confirmation) flush succeeds; the mint happens; the success
        # write succeeds; the SECOND (post-success) flush fails.
        code, out, err = self._run(
            self._approve_argv(), stdin_text=self._phrase() + "\n",
            stdout_flush_fail_indices={2},   # post-success flush fails
        )
        self.assertEqual(code, 2)
        self.assertIsNotNone(self._active())          # the approval WAS recorded
        self.assertNotIn("Approval recorded", err)    # no normal success claimed on stderr
        self.assertIn("approval-status", err)         # points to the verify command
        self.assertIn(self.bh, self._ledger_text())   # discoverable
        self.assertNotIn(self._approval_id(), out + err)  # no full approval_id printed
        self.assertEqual((self.root / "src" / "app.py").read_text(), "old\n")

    def test_stderr_flush_failure_still_exit_2_no_traceback(self):
        # A pre-mint error whose stderr write+flush both fail must still return 2.
        with mock.patch("cofferdam.approve_cli._mint",
                        side_effect=AssertionError("_mint must not be called")):
            code, out, err = self._run(
                ["approve", "--file", str(self.proposal_path), "--bogus"],
                stdin_text=self._phrase() + "\n",
                stderr_fail_indices={1}, stderr_flush_fail_indices={1},
            )
        self.assertEqual(code, 2)
        self.assertIsNone(self._active())

    def test_closed_stream_valueerror_flush_fails_closed(self):
        # A ValueError (I/O on closed file) from the pre-confirmation flush must
        # fail closed exactly like an OSError.
        with mock.patch("cofferdam.approve_cli._mint",
                        side_effect=AssertionError("_mint must not be called")):
            code, out, err = self._run(
                self._approve_argv(), stdin_text=self._phrase() + "\n",
                stdout_flush_fail_indices={1},
                stdout_flush_exc=ValueError("I/O operation on closed file"),
            )
        self.assertEqual(code, 2)
        self.assertEqual(self.last_stdin.readline_count, 0)
        self.assertIsNone(self._active())

    # -- helpers --
    def _ledger_text(self):
        return (self.root / ".cofferdam" / "approvals.jsonl").read_text(encoding="utf-8")

    def _approval_id(self):
        with _ApprovalStore(self.view).lock(create=False):
            entries = _ApprovalStore(self.view).read_entries()
        return entries[0]["approval_id"]


if __name__ == "__main__":
    unittest.main()
