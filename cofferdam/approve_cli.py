r"""``cofferdam approve`` — the human-mediated approval mint (PR3c1).

This is the **only supported path that creates authoritative approval state.** It
shows a human the exact bound change (target, risk, pre-state, binding digest,
and the complete exact patch) and, only after an explicit typed confirmation on
a real terminal, appends one authoritative five-minute single-use approval
record to the PR3b ledger. It **executes nothing**: no ``git apply``, no
subprocess, no Git invocation, no proposal-target mutation, no staging, no
committing. Execution is PR3c2; audit is PR3d.

**Authority boundary.** The supported surface is exactly ``cofferdam approve
--file <path>``. There is deliberately **no** public ``create_approval`` /
``mint_approval`` Python API. The authority-bearing mint (``_mint``) is an
unexported internal seam whose *only* production caller is ``approve_command``;
its keyword-only ``store`` / ``clock`` / ``token_hex`` parameters exist solely so
tests can inject deterministic doubles. The supported command exposes **no**
caller-controlled clock, store, lock, entropy source, TTL, ``approval_id``,
timestamp, state path, ``bound_hash``, or ``repo_root_id`` — it selects the
production ``SystemClock``, ``secrets.token_hex``, fixed ``TTL_SECONDS``, and
``_ApprovalStore(repo_view)`` internally. Underscore naming is **not** the
security boundary; the boundary is the supported interface plus the documented
host-access assumptions in ``SECURITY.md`` (an actor with arbitrary in-process
Python or direct ``.cofferdam/`` write access is outside v0.1 guarantees).

**Never trust caller-supplied binding.** The dry-run artifact is rebuilt from
``(Proposal, RepoView)`` via ``build_dry_run_artifact`` — twice: once for the
display, and once **under the ledger lock** immediately before the append. The
mint proceeds only if the full 64-hex ``bound_hash`` of the second build equals
the one the human saw, so a repository change during confirmation fails the
approval (the ledger lock serializes approval state, not repository files — an
approval can still go stale afterwards, which PR3c2 must re-check before it
consumes/executes).

**Injective escaped display.** The patch is shown through one deterministic,
reversible, ASCII-only escape grammar (``_escape_line``) so two different
``proposal.diff`` byte sequences can never render identically: a literal
backslash becomes ``\\``, a TAB becomes ``\t``, every trailing ASCII space
becomes ``\x20`` (so trailing whitespace is countable), and every non-ASCII code
point becomes ``\u{HEX}``. A separate header field states whether the patch ends
with a final ``LF``. This is display-only — ``patch_hash`` / ``bound_hash`` bind
the original exact UTF-8 bytes, never the rendered text.

**Terminal output is fail-safe.** Every write goes through ``_emit`` (or a
helper built on it), which swallows ``UnicodeEncodeError`` / stream failures with
no traceback. A failure to render the complete change or the prompt aborts
**before** any confirmation or mint. Untrusted content (the target path, error
detail) is never emitted raw: the path is escaped through the same grammar, and
exception strings are replaced with fixed bounded categories.

**Indeterminate durability.** If the approval record is completely written but
its ``fsync`` then fails (``LedgerDurabilityError``), the mint does *not* claim
the approval failed: it exits non-zero with a bounded warning that the state is
indeterminate and points the human at ``approval-status``. The same posture
applies if the record was fsynced but the success message could not be written.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .approval import TTL_SECONDS, ApprovalError, ApprovalView, fold_active, find_valid_approval
from .approval_store import _ApprovalStore, LedgerDurabilityError
from .clock import Clock, SystemClock
from .dryrun import DryRunArtifact, DryRunError, build_dry_run_artifact
from .paths import normalize_target
from .proposal import parse_proposal
from .repo_view import RepoReadError, RepoView, FilesystemRepoView

PROGRAM = "cofferdam approve"

EXIT_OK = 0        # approval appended and fsynced
EXIT_DECLINE = 1   # declined / mismatch / EOF / interrupt / already-active
EXIT_ERROR = 2     # usage / non-TTY / input / guard / render / state-change / ledger / system

# Bounded reads.
MAX_PROPOSAL_BYTES = 2 * 1024 * 1024   # 2 MiB (diff cap is 1 MiB; this bounds JSON overhead)
MAX_CONFIRM_BYTES = 256                 # confirmation line cap, enforced in UTF-8 BYTES

CONFIRM_PREFIX_LEN = 12                 # first 12 hex of bound_hash in the confirmation phrase
CONFIRM_KEYWORD = "APPROVE"

# --- terminal-safety screen (frozen reject set) ------------------------------
# Any code point outside this policy makes the proposal UNAPPROVABLE in v0.1
# (fail closed, exit 2) — the human must approve the exact bytes, and a faithful
# terminal rendering of these is impossible. LF (line separator) and TAB (marked
# visibly, below) are the only permitted C0 controls. Combining marks are allowed
# (visible, meaningful); NFC/NFD normalization is never performed.

_BIDI = frozenset(
    {0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2066, 0x2067, 0x2068, 0x2069}
)
_ZERO_WIDTH = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF})


def _bad_char_class(text: str) -> Optional[str]:
    """Return the name of the first code point that fails the terminal-safety
    screen, or ``None`` if every character is renderable. Never returns the
    offending character itself (it must not reach the terminal)."""
    for ch in text:
        cp = ord(ch)
        if cp == 0x0A or cp == 0x09:
            continue  # LF and TAB are the only permitted C0 controls
        if cp < 0x20:
            return "a C0 control character (including CR/ESC)"
        if cp == 0x7F:
            return "the DEL control character"
        if 0x80 <= cp <= 0x9F:
            return "a C1 control character"
        if 0xD800 <= cp <= 0xDFFF:
            return "a surrogate code point"
        if cp in _BIDI:
            return "a Unicode bidirectional-formatting character"
        if cp in _ZERO_WIDTH:
            return "a zero-width/invisible character"
        if 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE:
            return "a Unicode noncharacter"
    return None


def _screen_renderable(text: str) -> bool:
    """True iff ``text`` contains only characters the display can render
    faithfully (see ``_bad_char_class``)."""
    return _bad_char_class(text) is None


def _escape_field(text: str) -> str:
    r"""Escape a single-line display field (e.g. the target path) into an
    injective, ASCII-only form: a literal backslash becomes ``\\``, a TAB becomes
    ``\t``, and every non-ASCII code point becomes ``\u{HEX}``. Interior ASCII
    spaces stay literal (a single-line field has no trailing-space semantics).
    The only source of a backslash in the output is an escape sequence, so the
    mapping is reversible."""
    out = []
    for ch in text:
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\t":
            out.append("\\t")
        elif cp < 0x80:
            out.append(ch)  # printable ASCII (the render screen removed controls)
        else:
            out.append(f"\\u{{{cp:x}}}")
    return "".join(out)


def _escape_line(line: str) -> str:
    r"""Display-only, **injective** rendering of one logical patch line.

    Builds on ``_escape_field`` and additionally renders every **trailing** ASCII
    space as ``\x20`` so trailing whitespace is visible and countable. Distinct
    ``proposal.diff`` byte sequences always yield distinct output: a literal
    ``\t`` in the source renders as ``\\t`` (vs an actual TAB's ``\t``), a literal
    ``\x20`` renders as ``\\x20`` (vs a trailing space's ``\x20``), and a literal
    ``→`` (U+2192) renders as ``\u{2192}`` with no TAB meaning. The bytes bound by
    ``patch_hash`` / ``bound_hash`` are never touched."""
    stripped = line.rstrip(" ")
    trailing = len(line) - len(stripped)
    return _escape_field(stripped) + ("\\x20" * trailing)


def _emit(stream, text: str) -> bool:
    """Write ``text`` to a terminal stream, swallowing an encoding or stream
    failure with **no traceback**. Returns ``True`` iff the whole write
    succeeded. Used for every stdout/stderr write so a terminal that cannot
    encode a character (``UnicodeEncodeError``) or a broken pipe never produces a
    Python traceback — and, before confirmation, never lets a partial display be
    followed by a mint."""
    try:
        stream.write(text)
        return True
    except (UnicodeEncodeError, OSError, ValueError):
        return False


def _flush_stream(stream) -> bool:
    """Flush ``stream``, swallowing a flush failure with **no traceback**.
    Returns ``True`` **only** when the flush succeeds. A silently-swallowed flush
    failure is forbidden on any authority-relevant path: on a fully buffered
    stream the flush is what actually delivers the written bytes to the terminal,
    so confirmation must never be accepted until a checked flush of the complete
    display and prompt has succeeded."""
    try:
        stream.flush()
        return True
    except (UnicodeEncodeError, OSError, ValueError):
        return False


# --- typed control-flow signals ----------------------------------------------


class _ActiveDuplicate(Exception):
    """An active approval already exists for this binding (exit 1; mint nothing)."""

    def __init__(self, view: ApprovalView) -> None:
        super().__init__("an active approval already exists")
        self.view = view


class _StateChanged(Exception):
    """The rebuilt binding under the lock no longer matches what the human saw
    (exit 2; mint nothing)."""


@dataclass(frozen=True)
class _MintResult:
    relative_path: str
    bound_hash: str
    created_at: int
    expires_at: int


# --- output helpers ----------------------------------------------------------


# Fixed, bounded, ASCII-safe warning for the indeterminate-durability case: the
# record may already be a complete active approval even though the command is
# exiting non-zero. It must NOT claim the approval failed, and it points the
# human at the real, read-only ``approval-status`` command (exact existing
# syntax) or expiry. Retrying may hit an active duplicate.
_INDETERMINATE_MSG = (
    "approval state is indeterminate: the approval record may already exist and "
    "be active. No file was modified. Do NOT assume that approval failed. Check "
    "it with 'cofferdam approval-status --file <path>', or wait at least five "
    "minutes for expiry before retrying (a retry may report an active duplicate)."
)

# The record WAS written and fsynced; only the success message could not be
# shown. An active approval definitely exists — same verify-or-wait posture.
_RECORDED_NO_DISPLAY_MSG = (
    "the approval was recorded and flushed, but its confirmation message could "
    "not be displayed. An active approval exists and no file was modified. "
    "Verify with 'cofferdam approval-status --file <path>'; do NOT retry blindly "
    "(a retry may report an active duplicate)."
)


def _err(message: str) -> int:
    # Best-effort delivery: write then flush the diagnostic (so a buffered stderr
    # still shows the safety-critical indeterminate warning). A failure to write
    # OR flush the message never changes the authority decision — the exit code
    # is fixed and no mint is retried.
    _emit(sys.stderr, f"{PROGRAM}: {message}\n")
    _flush_stream(sys.stderr)
    return EXIT_ERROR


def _decline(message: str) -> int:
    _emit(sys.stderr, f"{PROGRAM}: {message}\n")
    _flush_stream(sys.stderr)
    return EXIT_DECLINE


# --- argument + proposal loading ---------------------------------------------


class _UsageError(Exception):
    pass


def _parse_args(args: Sequence[str]) -> str:
    """Return the proposal file path, or raise ``_UsageError``. The only accepted
    form is ``--file <path>`` — no stdin, no ``-``, no other flag."""
    argv = list(args)
    if len(argv) != 2 or argv[0] != "--file":
        raise _UsageError("usage: cofferdam approve --file <path>")
    path = argv[1]
    if path == "" or path == "-":
        raise _UsageError(
            "approve reads the proposal only from a file; stdin/'-' is not accepted"
        )
    return path


def _load_proposal_text(path: str) -> str:
    """Open the proposal file **exactly once**, bounded, and return its text.

    Rejects a non-regular file (directory / FIFO / device / socket) before
    reading. Symlinks are permitted (``os.stat`` follows to the real target,
    which must be a regular file). Fails closed (``OSError``/``ValueError``) on
    any I/O error, over-size, or invalid UTF-8."""
    try:
        info = os.stat(path)  # follows symlinks: a symlink to a regular file is OK
    except OSError as exc:
        # Never echo the OS error string or the path: either can carry the
        # untrusted filename (or terminal-control bytes) unscreened to the
        # terminal. A fixed bounded category is enough for the human.
        raise ValueError(
            "cannot read proposal file (missing, unreadable, or not a regular file)"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("proposal --file must be a regular file")
    # One and only one open of the proposal content.
    with open(path, "rb") as handle:
        data = handle.read(MAX_PROPOSAL_BYTES + 1)
    if len(data) > MAX_PROPOSAL_BYTES:
        raise ValueError("proposal file exceeds the maximum size")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("proposal file is not valid UTF-8") from exc


# --- confirmation ------------------------------------------------------------


def _read_confirmation(expected: str) -> bool:
    """Read exactly one bounded confirmation line from stdin and compare it,
    case-sensitively, to ``expected``. Returns ``True`` only on an exact match.

    The length limit is the frozen **256 UTF-8 bytes** (not Python characters):
    a small bounded number of characters is read (a ≤256-byte line is ≤256
    characters, since UTF-8 uses at least one byte per character), then the line
    is UTF-8 encoded and rejected if it exceeds ``MAX_CONFIRM_BYTES`` bytes.
    Whitespace is never stripped into acceptance. Fails closed on EOF, a missing
    terminator (over-length), any over-byte-limit line, or any embedded control
    character. Exactly one attempt; the caller does not retry."""
    # Read at most MAX_CONFIRM_BYTES + 2 characters. A valid (≤256-byte) line is
    # at most 256 characters plus its newline, so this budget always captures the
    # terminator of an in-limit line; a longer line arrives without a newline.
    raw = sys.stdin.readline(MAX_CONFIRM_BYTES + 2)
    if raw == "":
        return False  # EOF
    if not raw.endswith("\n"):
        return False  # no terminator within the budget => over-length, reject
    # Strip exactly one trailing LF and one preceding CR (Windows console).
    line = raw[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    # Enforce the frozen limit in UTF-8 BYTES.
    if len(line.encode("utf-8")) > MAX_CONFIRM_BYTES:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in line):
        return False  # embedded control character
    return line == expected


# --- display -----------------------------------------------------------------


def _prestate_summary(repo_view: RepoView, parts) -> str:
    """A non-sensitive pre-state line: existence + byte count only (never
    content or a content hash). ``build_dry_run_artifact`` already proved the
    target readable, so this re-read is for display only."""
    try:
        data = repo_view.read_bytes(parts)
    except RepoReadError:
        return "unavailable"
    if data is None:
        return "target does not exist (this change creates it)"
    return f"target exists ({len(data)} bytes)"


def _render_display(artifact: DryRunArtifact, proposal, prestate_line: str) -> str:
    """Build the complete, injective, ASCII-only review display for the human.

    The patch is escaped line-by-line (``_escape_line``); a header field states
    whether the original bytes end with a final ``LF``. The rendering is
    display-only: ``patch_bytes`` and the ``bound_hash`` shown are those of the
    original exact ``proposal.diff`` UTF-8 bytes."""
    diff = proposal.diff
    patch_bytes = len(diff.encode("utf-8"))
    ends_with_lf = diff.endswith("\n")
    lines = diff.split("\n")
    if ends_with_lf and lines and lines[-1] == "":
        lines = lines[:-1]  # drop the empty artifact after the final LF
    patch_lines = len(lines)
    header = (
        f"{PROGRAM} -- review the exact change before approving\n"
        f"\n"
        f"  target:       {_escape_field(artifact.relative_path)}\n"
        f"  risk:         {artifact.guard_risk}   (display-only; does not authorize)\n"
        f"  pre-state:    {prestate_line}\n"
        f"  patch size:   {patch_bytes} bytes, {patch_lines} lines\n"
        f"  ends with LF: {'yes' if ends_with_lf else 'no'}\n"
        f"  repository:   {artifact.repo_root_id[:12]}...\n"
        f"  binding:      {artifact.bound_hash}\n"
        f"                (binds the exact patch bytes + target + pre-state + repository)\n"
        f"\n"
        f"--- BEGIN ESCAPED PATCH ({patch_bytes} bytes; \\\\=backslash \\t=TAB "
        f"\\x20=trailing space \\u{{HEX}}=non-ASCII) ---\n"
    )
    body = "\n".join(_escape_line(ln) for ln in lines)
    footer = "\n--- END ESCAPED PATCH ---\n"
    return header + body + footer


# --- internal authority-bearing mint (UNSUPPORTED seam) ----------------------


def _mint(
    proposal,
    repo_view: RepoView,
    *,
    store: _ApprovalStore,
    clock: Clock,
    token_hex: Callable[[int], str],
    expected_bound_hash: str,
) -> _MintResult:
    """Append exactly one authoritative approval record, under the exclusive
    ledger lock, for the freshly-rebuilt artifact — fail-closed.

    **Unsupported internal seam.** Not exported, not in ``__all__``; its only
    production caller is ``approve_command`` (which supplies the production
    ``SystemClock`` / ``_ApprovalStore`` / ``secrets.token_hex``). The keyword
    dependencies exist solely for deterministic tests.

    Raises ``_StateChanged`` if the repository moved under the human,
    ``_ActiveDuplicate`` if an active approval already exists, ``ApprovalError``
    on entropy exhaustion / an ``approval_id`` collision / a torn or failed
    write, and ``LedgerDurabilityError`` (an ``OSError`` subclass) if the record
    was fully written but its ``fsync`` failed (indeterminate durability).
    Success is returned only after the append is fsynced.
    """
    with store.lock(create=True):
        # Rebuild the binding under the lock from the SAME immutable Proposal and
        # RepoView — never a re-read of the proposal file, never caller-supplied.
        artifact = build_dry_run_artifact(proposal, repo_view)
        if artifact.bound_hash != expected_bound_hash:
            raise _StateChanged()

        now = int(clock.now_epoch())
        root_id = artifact.repo_root_id  # == the current repo_root_id
        entries = store.read_entries()
        active = fold_active(entries, root_id, now)
        existing = active.get(artifact.bound_hash)
        if existing is not None:
            raise _ActiveDuplicate(existing)

        # Generate the event identifier internally. A 256-bit collision signals a
        # broken entropy source and must fail closed with no retry.
        existing_ids = {
            e["approval_id"] for e in entries if e["entry_type"] == "approval"
        }
        try:
            approval_id = token_hex(32)
        except Exception as exc:  # entropy exhaustion / generator failure
            raise ApprovalError("could not generate an approval identifier") from exc
        if not isinstance(approval_id, str) or len(approval_id) != 64:
            raise ApprovalError("approval identifier has the wrong shape")
        if approval_id in existing_ids:
            raise ApprovalError("approval identifier collision")

        entry = {
            "entry_type": "approval",
            "schema_version": artifact.schema_version,
            "approval_id": approval_id,
            "bound_hash": artifact.bound_hash,
            "repo_root_id": artifact.repo_root_id,
            "relative_path": artifact.relative_path,
            "guard_risk": artifact.guard_risk,
            "created_at": now,
            "expires_at": now + TTL_SECONDS,
        }
        store._append_approval(entry)  # validates + write-all + fsync under the lock
        return _MintResult(
            relative_path=artifact.relative_path,
            bound_hash=artifact.bound_hash,
            created_at=now,
            expires_at=now + TTL_SECONDS,
        )


# --- supported command handler -----------------------------------------------


def approve_command(args: Sequence[str]) -> int:
    """``cofferdam approve --file <path>`` — the sole supported approval-mint
    path. Returns a process exit code; performs no execution and mutates no
    proposal target."""
    # 1. Parse argv (usage errors need no terminal).
    try:
        path = _parse_args(args)
    except _UsageError as exc:
        return _err(str(exc))

    # 2. TTY gate — all three streams must be interactive terminals. Checked
    #    before the proposal is read so redirected/piped/CI invocations refuse
    #    early. A pseudo-TTY satisfies isatty(); this is an intent/anti-automation
    #    layer, not proof of a human (see SECURITY.md).
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()
    except (ValueError, OSError):
        interactive = False
    if not interactive:
        return _err("approve requires an interactive terminal (stdin, stdout, stderr)")

    # 3. Load + parse the proposal (opened exactly once). Error text is a fixed
    #    bounded category — never the raw exception string (it can carry the
    #    untrusted path/OS filename unscreened to the terminal). The
    #    ``parse_proposal`` reasons ARE a controlled enum vocabulary (safe).
    try:
        raw = _load_proposal_text(path)
    except (OSError, ValueError):
        return _err("cannot read the proposal file (missing, unreadable, oversized, or not UTF-8)")
    try:
        payload = json.loads(raw)
    except ValueError:
        return _err("proposal file is not valid JSON")
    parsed = parse_proposal(payload)
    if not parsed.ok:
        reasons = ",".join(r.value for r in parsed.reasons)
        return _err(f"proposal rejected: {reasons}")
    proposal = parsed.proposal

    # 4. Production RepoView for the actual repository (root = current directory).
    try:
        repo_view = FilesystemRepoView(os.getcwd())
    except ValueError:
        return _err("cannot open the repository root (run approve from the repository root)")

    # 5. Build the display binding from (Proposal, RepoView) — never a caller value.
    #    A guard BLOCKED or canonicalization failure surfaces a fixed message; the
    #    underlying exception string may embed a path and is never echoed.
    try:
        artifact_1 = build_dry_run_artifact(proposal, repo_view)
    except DryRunError:
        return _err(
            "cannot build the exact change: the guard blocked it or the target "
            "path is unsafe/unresolvable; this proposal is not approvable"
        )
    except Exception:  # fail closed on any unexpected build error
        return _err("cannot build the exact change")

    # 6. Terminal-safety screen (diff + path). Unrenderable content is
    #    unapprovable in v0.1 — never approve a truncated/normalized rendering.
    bad = _bad_char_class(proposal.diff) or _bad_char_class(artifact_1.relative_path)
    if bad is not None:
        return _err(
            "the exact change cannot be displayed safely: it contains "
            f"{bad}; this proposal is not approvable in this version"
        )

    # 7. Early read-only duplicate check (UX only; the authoritative check runs
    #    under the lock in _mint). A fixed message — the ApprovalError string is
    #    not echoed.
    try:
        already = find_valid_approval(artifact_1.bound_hash, repo_view)
    except ApprovalError:
        return _err("cannot read the approval ledger (it may be corrupt or locked)")
    if already is not None:
        return _decline(
            "an active approval already exists for this change; nothing to do "
            "(it expires shortly)"
        )

    # 8. Display the exact change. If the complete display cannot be written
    #    (encoding/stream failure), abort BEFORE any confirmation or mint — a
    #    partial display must never be followed by minting. The escaped patch is
    #    ASCII-only, so an encoding failure here is unexpected but still safe.
    norm = normalize_target(proposal.target_path)
    parts = norm.path.parts if norm.ok else ()
    prestate_line = _prestate_summary(repo_view, parts)
    try:
        display = _render_display(artifact_1, proposal, prestate_line)
    except Exception:  # fail closed on any unexpected render error — no mint
        return _err("could not render the exact change")
    if not _emit(sys.stdout, display):
        return _err("could not display the exact change on this terminal; nothing was approved")

    # 9. One confirmation attempt, exact case-sensitive phrase. If the prompt
    #    itself cannot be written, abort before reading/minting.
    expected = f"{CONFIRM_KEYWORD} {artifact_1.bound_hash[:CONFIRM_PREFIX_LEN]}"
    if not _emit(
        sys.stdout,
        f"\nTo approve exactly this change, type: {expected}\n"
        f"Anything else declines. > ",
    ):
        return _err("could not display the confirmation prompt; nothing was approved")
    # Fail closed if the flush fails: on a fully buffered stream the flush is what
    # actually delivers the complete display + prompt to the human, so no
    # confirmation may be read (and no lock/mint may follow) until it succeeds.
    if not _flush_stream(sys.stdout):
        return _err("could not deliver the complete change to this terminal; nothing was approved")
    try:
        confirmed = _read_confirmation(expected)
    except KeyboardInterrupt:
        return _decline("cancelled")
    except (EOFError, OSError, ValueError):
        return _decline("no confirmation received")
    if not confirmed:
        return _decline("declined (confirmation did not match)")

    # 10. Mint under the ledger lock (rebuild + full-hash equality + append + fsync).
    try:
        result = _mint(
            proposal,
            repo_view,
            store=_ApprovalStore(repo_view),
            clock=SystemClock(),
            token_hex=secrets.token_hex,
            expected_bound_hash=artifact_1.bound_hash,
        )
    except _ActiveDuplicate:
        return _decline(
            "an active approval already exists for this change; nothing appended"
        )
    except _StateChanged:
        return _err(
            "the repository changed during confirmation; nothing was approved. "
            "Re-run approve to review the new state"
        )
    except LedgerDurabilityError:
        # The record was fully written but its fsync failed: an active approval
        # MAY already exist. Never claim it failed — exit 2 with the bounded
        # indeterminate warning (checked before the generic ApprovalError/OSError
        # handlers, since LedgerDurabilityError subclasses both).
        return _err(_INDETERMINATE_MSG)
    except ApprovalError:
        # Entropy failure, id collision, or a torn/failed write (nothing usable
        # was persisted — the PR3b fold invalidates a torn tail on the next
        # read). Fixed message; the exception string is not echoed.
        return _err("could not record the approval (it was not written; nothing was approved)")
    except Exception:
        # Any other failure under the lock fails closed with a bounded message
        # and no traceback; nothing usable was persisted.
        return _err("could not record the approval (an I/O or system error occurred; nothing was approved)")

    # The record is written and fsynced. If the success message cannot be written
    # OR cannot be flushed, the approval STILL exists — use the
    # indeterminate-authority posture rather than a traceback or a false
    # "recorded" line the human never actually saw.
    ttl = result.expires_at - result.created_at
    if not _emit(
        sys.stdout,
        "\nApproval recorded. No file was modified.\n"
        f"  target:   {_escape_field(result.relative_path)}\n"
        f"  binding:  {result.bound_hash}\n"
        f"  expires:  in {ttl} seconds (single use)\n",
    ):
        return _err(_RECORDED_NO_DISPLAY_MSG)
    if not _flush_stream(sys.stdout):
        return _err(_RECORDED_NO_DISPLAY_MSG)
    return EXIT_OK
