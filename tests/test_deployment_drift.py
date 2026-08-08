"""Structural guards on where the production daemon actually runs from.

Background — what actually happened
-----------------------------------
Every milestone since M2A validated its work by dropping a
``cofferdam-workstation.service.d/<n>-<milestone>-validation.conf`` that
overrode ``ExecStart`` and ``WorkingDirectory`` to point at that milestone's
feature worktree. None of them were removed afterwards. Twelve had accumulated
by M2H, and because systemd applies drop-ins in lexical order the highest number
wins — so the production daemon had been running M2G-era code out of
``clones/claude-code-adapter`` ever since PR #21's validation, with
``--enable-validation-task-adapter`` still on.

Nothing caught it, because every existing test asserts against the *shipped*
unit in ``deploy/`` and that file was always correct. The drift lived entirely
in the installed drop-in directory.

Three consequences, all of which M2H PR4 had to fix before an unattended-reboot
test could mean anything:

* the deployed code was not the merged code, so a phone rebooting into
  production would find no Remote Control card at all;
* a validation-only task adapter was enabled on a normal runtime, which the
  daemon itself prints a warning about on every start;
* production's survival depended on a *feature worktree* — a directory nobody
  had promised to keep, whose deletion would have left the daemon failing to
  start at every boot with nothing obviously wrong.

What these tests can and cannot do
----------------------------------
They assert the **repository's** contract: the shipped unit names the A/B slot,
no shipped file ships a validation drop-in, and the deployment documentation
tells an operator to remove them. They deliberately do **not** read
``~/.config/systemd/user`` — a test that passes or fails depending on the
machine it runs on is not a test, and CI has no such directory. The installed
state is checked by ``docs/DEPLOYMENT_PREFLIGHT.md`` and the read-only script
beside it, which a person runs before trusting a reboot.

Standard-library only, so this runs on the stdlib-only CI path.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = REPO_ROOT / "deploy"
DOCS_ROOT = REPO_ROOT / "docs"

WORKSTATION_UNIT = DEPLOY_ROOT / "cofferdam-workstation.service"
RC_UNIT = DEPLOY_ROOT / "cofferdam-rc@.service"
PREFLIGHT_DOC = DOCS_ROOT / "DEPLOYMENT_PREFLIGHT.md"
PREFLIGHT_SCRIPT = DEPLOY_ROOT / "preflight.sh"

#: The one place a production unit may run from. Not a worktree, not a clone.
SLOT_PREFIX = "%h/cofferdam/slots/"

#: Directories that hold *development* checkouts. A production ExecStart naming
#: one of these is the drift this module exists to prevent.
DEVELOPMENT_ROOTS = ("clones/", "worktrees/")

#: Flags that must never be enabled by a shipped production unit. The daemon
#: prints "Do not leave this enabled on a normal runtime" for the first one, and
#: a unit that ships it makes that warning permanent.
VALIDATION_ONLY_FLAGS = (
    "--enable-validation-task-adapter",
)


def unit_directives(path: Path, name: str) -> list:
    """Every value assigned to ``name`` in that unit file, comments stripped."""
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == name:
            values.append(value.strip())
    return values


class ShippedUnitTargetsTheSlot(unittest.TestCase):
    """The shipped units run from the A/B slot and nowhere else."""

    def test_the_workstation_unit_runs_from_a_slot(self) -> None:
        for directive in ("ExecStart", "WorkingDirectory"):
            with self.subTest(directive=directive):
                values = [v for v in unit_directives(WORKSTATION_UNIT, directive) if v]
                self.assertTrue(values, f"{directive} must be set")
                for value in values:
                    self.assertIn(SLOT_PREFIX, value)

    def test_the_remote_control_unit_runs_from_the_same_slot(self) -> None:
        """A slot switch has to move the daemon and its hosts together."""
        for directive in ("ExecStart", "WorkingDirectory"):
            with self.subTest(directive=directive):
                for value in unit_directives(RC_UNIT, directive):
                    if value:
                        self.assertIn(SLOT_PREFIX, value)

    def test_no_shipped_unit_names_a_development_checkout(self) -> None:
        """The exact drift: production pinned to a feature worktree."""
        for path in sorted(DEPLOY_ROOT.glob("*.service")):
            body = path.read_text(encoding="utf-8")
            code = "\n".join(
                line for line in body.splitlines() if not line.strip().startswith("#")
            )
            for root in DEVELOPMENT_ROOTS:
                with self.subTest(unit=path.name, root=root):
                    self.assertNotIn("cofferdam/" + root, code)

    def test_no_shipped_unit_enables_a_validation_only_adapter(self) -> None:
        for path in sorted(DEPLOY_ROOT.glob("*.service")):
            code = "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("#")
            )
            for flag in VALIDATION_ONLY_FLAGS:
                with self.subTest(unit=path.name, flag=flag):
                    self.assertNotIn(flag, code)

    def test_no_hardcoded_home_or_username(self) -> None:
        """``%h``, never ``/home/<somebody>``."""
        for path in sorted(DEPLOY_ROOT.glob("*.service")):
            code = "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("#")
            )
            with self.subTest(unit=path.name):
                self.assertNotIn("/home/", code)
                self.assertNotIn("User=", code)

    def test_no_token_or_secret_is_named_in_a_unit(self) -> None:
        for path in sorted(DEPLOY_ROOT.glob("*.service")):
            code = "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("#")
            )
            for forbidden in ("COFFERDAM_TOKEN", "Bearer ", "password", "secret="):
                with self.subTest(unit=path.name, token=forbidden):
                    self.assertNotIn(forbidden, code)


class BoundedRestartPolicy(unittest.TestCase):
    """A daemon that waits for Tailscale must not exhaust its start limit first."""

    def test_the_workstation_unit_restarts_on_failure(self) -> None:
        self.assertIn("on-failure", unit_directives(WORKSTATION_UNIT, "Restart"))

    def test_the_restart_policy_is_bounded_and_not_busy(self) -> None:
        delay = unit_directives(WORKSTATION_UNIT, "RestartSec")
        self.assertTrue(delay, "RestartSec must be set so a retry is not a busy loop")
        seconds = int(re.sub(r"[^0-9]", "", delay[0]) or 0)
        self.assertGreaterEqual(seconds, 1)

    def test_the_start_limit_leaves_room_for_the_bind_wait(self) -> None:
        """The interlock that matters at boot.

        The daemon waits for its Tailscale address in-process, so a slow
        ``tailscaled`` costs one long start rather than many failed ones. The
        start limit still has to be generous enough that a genuinely absent
        address does not burn the unit into a permanent failure before somebody
        can look at it.
        """
        burst = unit_directives(WORKSTATION_UNIT, "StartLimitBurst")
        interval = unit_directives(WORKSTATION_UNIT, "StartLimitIntervalSec")
        self.assertTrue(burst and interval, "both start-limit keys must be explicit")
        self.assertGreaterEqual(int(burst[0]), 3)

    def test_the_start_limit_keys_are_in_the_unit_section(self) -> None:
        """Under ``[Service]`` systemd ignores them with only a warning, so a
        rate limit can look configured while doing nothing.

        Sections are split on a real header line, not on the first occurrence of
        the text ``[Service]`` — the ``[Unit]`` section explains this very rule
        in a comment, and a naive split truncates the section at the
        explanation.
        """
        section = None
        found = {}
        for raw in WORKSTATION_UNIT.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            if line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if key.startswith("StartLimit"):
                found[key] = section
        for key in ("StartLimitBurst", "StartLimitIntervalSec"):
            with self.subTest(key=key):
                self.assertEqual(found.get(key), "[Unit]")


class NoPublicBindFallback(unittest.TestCase):
    """A private service that cannot reach its private interface stays down."""

    def test_the_entry_point_never_falls_back_to_another_address(self) -> None:
        source = (REPO_ROOT / "cofferdam" / "workstation" / "__main__.py").read_text(
            encoding="utf-8"
        )
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in ('"0.0.0.0"', "'0.0.0.0'", '"::"'):
            with self.subTest(address=forbidden):
                self.assertNotIn(forbidden, code)

    def test_no_shipped_unit_configures_a_public_bind(self) -> None:
        for path in sorted(DEPLOY_ROOT.glob("*.service")):
            with self.subTest(unit=path.name):
                self.assertNotIn("0.0.0.0", path.read_text(encoding="utf-8"))


class PreflightIsDocumentedAndReadOnly(unittest.TestCase):
    """The installed state is a machine fact, so a person checks it, not CI."""

    def test_the_preflight_document_exists(self) -> None:
        self.assertTrue(PREFLIGHT_DOC.is_file())

    def test_it_tells_an_operator_to_clear_stale_validation_drop_ins(self) -> None:
        body = PREFLIGHT_DOC.read_text(encoding="utf-8")
        self.assertIn("cofferdam-workstation.service.d", body)
        self.assertIn("validation", body.lower())

    def test_it_documents_linger_and_its_rollback(self) -> None:
        body = PREFLIGHT_DOC.read_text(encoding="utf-8")
        self.assertIn("enable-linger", body)
        self.assertIn("disable-linger", body)

    @staticmethod
    def _executed_lines() -> list:
        """The script's lines that actually *run* something.

        Comments are dropped, and so is anything whose whole job is to print:
        the script deliberately tells an operator "fix: loginctl enable-linger",
        and a guard that cannot tell advice from action would force that advice
        to be deleted — making the tool less useful in order to make the test
        pass. What remains is what bash would execute.
        """
        lines = []
        for raw in PREFLIGHT_SCRIPT.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("printf", "echo", "pass ", "warn ", "fail ")):
                continue
            lines.append(line)
        return lines

    def test_the_preflight_script_exists_and_changes_nothing(self) -> None:
        """Read-only by construction: it may inspect, and may not act."""
        self.assertTrue(PREFLIGHT_SCRIPT.is_file())
        executed = "\n".join(self._executed_lines())
        for forbidden in (
            "systemctl --user start",
            "systemctl --user stop",
            "systemctl --user restart",
            "systemctl --user enable",
            "loginctl enable-linger",
            "loginctl disable-linger",
            "daemon-reload",
            "rm ",
            "mv ",
            "reboot",
            "sudo",
            ">>",
            "tee ",
        ):
            with self.subTest(command=forbidden):
                self.assertNotIn(forbidden, executed)

    def test_the_preflight_script_reads_no_secret_value(self) -> None:
        """It may check that the token *file* exists and is 0600. It may never
        look at what is inside it."""
        executed = self._executed_lines()
        joined = "\n".join(executed)
        self.assertIn("stat -c %a", joined)
        self.assertNotIn("COFFERDAM_TOKEN", joined)

        # Every line that mentions the token file, checked individually: the
        # file may be tested for existence and have its mode read, and that is
        # all. A blanket ban on "read" would also catch `while read -r f` over
        # a list of drop-in paths, which is unrelated and harmless.
        for line in executed:
            if "token_file" not in line and "secrets/token" not in line:
                continue
            with self.subTest(line=line):
                allowed = (
                    line.startswith("token_file=")
                    or line.startswith("if [ -f ")
                    or "stat -c %a" in line
                )
                self.assertTrue(allowed, "the token file may only be stat-ed")
                for forbidden in ("cat ", "head ", "tail ", "$(<"):
                    self.assertNotIn(forbidden, line)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
